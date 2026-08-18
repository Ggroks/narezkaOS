"""Стадия candidates: первый проход воронки отбора моментов.

BAZA.md §11. Дешёвые сигналы по всему материалу порождают кандидатов,
дорогая LLM работает уже только по ним. Здесь используются два самых
дешёвых признака: всплеск громкости и плотность речи.

Это осознанно грубый проход. §13 предупреждает: громкий крик без контекста
может иметь низкий score, и разделить «интересно зрителю» и «просто громко»
на этом этапе нечем. Задача — не выбрать лучшее, а сузить восемь часов
до сотни окон, не потеряв стоящее.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from narezka.core.artifacts import Artifact
from narezka.core.signals import (
    chat_rate,
    chat_reaction_share,
    forward_average,
    deduplicate,
    limit_coverage,
    find_peaks,
    loudness_track,
    read_wav_mono,
    robust_z,
    snap_to_segments,
    speech_density,
)
from narezka.core.stage import Device, Stage, StageContext, StageSkipped
from narezka.core.transcript import usable_segments
from narezka.stages.chat import CHAT_NAME
from narezka.stages.extract_audio import AUDIO_NAME
from narezka.stages.transcribe import TRANSCRIPT_NAME

CANDIDATES_NAME = "candidates.json"


class CandidatesStage(Stage):
    name = "candidates"
    #: v4 — приветствия в начале записи можно не учитывать.
    version = 4
    device = Device.ANY
    description = "Отбор кандидатов по всплескам громкости, речи и чата"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        inputs = [
            Artifact(ctx.paths.audio / AUDIO_NAME),
            Artifact(ctx.paths.transcript / TRANSCRIPT_NAME),
        ]
        # Чат необязателен: у записи может не быть чата вовсе. Указан входом,
        # чтобы его появление пересчитало кандидатов.
        chat = Artifact(ctx.paths.analysis / CHAT_NAME)
        if chat.exists():
            inputs.append(chat)
        return inputs

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.analysis / CANDIDATES_NAME)]

    @staticmethod
    def _ignore_start(ctx: StageContext) -> float:
        """Сколько секунд в начале записи не доверять чату. Ноль — доверять."""
        cfg = ctx.config.candidates
        return cfg.chat_ignore_start_seconds if cfg.chat_ignore_start else 0.0

    def config_slice(self, ctx: StageContext) -> dict:
        return {
            **ctx.config.candidates.model_dump(),
            "ignore_start": self._ignore_start(ctx),
            "min_duration": ctx.config.output.short.min_duration,
            "max_duration": ctx.config.output.short.max_duration,
            "optimal_duration": ctx.config.output.short.optimal_duration,
            "max_candidates": ctx.config.funnel.max_candidates,
            "dedup_overlap": ctx.config.funnel.dedup_overlap,
        }

    def run(self, ctx: StageContext) -> None:
        cfg = ctx.config.candidates
        short = ctx.config.output.short
        funnel = ctx.config.funnel

        transcript = Artifact(ctx.paths.transcript / TRANSCRIPT_NAME).read_json()
        segments = usable_segments(transcript.get("segments", []))
        if not segments:
            raise StageSkipped("в транскрипте нет пригодных сегментов речи")

        samples, rate = read_wav_mono(ctx.paths.audio / AUDIO_NAME)
        track = loudness_track(samples, rate, cfg.window_seconds)
        if track.count == 0:
            raise StageSkipped("звук слишком короткий для анализа")

        loudness_z = robust_z(track.rms_db)
        density = speech_density(segments, track.count, cfg.window_seconds)
        density_z = robust_z(density)

        chat, chat_z, reaction_z, chat_stats = self._chat_signal(ctx, track.count, cfg)

        # Сигнал участвует, если он и доступен, и включён. Вес выбывшего
        # не пропадает, а распределяется между остальными: иначе запись без
        # чата получала бы систематически более низкие оценки просто из-за
        # отсутствия источника, а выключенный вручную сигнал вёл бы себя как
        # измеренный ноль и тянул оценку вниз.
        active: dict[str, tuple[float, Any]] = {}
        if cfg.use_loudness:
            active["loudness"] = (cfg.loudness_weight, loudness_z)
        if cfg.use_speech_rate:
            active["density"] = (cfg.density_weight, density_z)
        if chat_z is not None and cfg.use_chat:
            active["chat"] = (cfg.chat_weight, chat_z)
        if reaction_z is not None and cfg.use_chat_reactions:
            # Доля реакций весит меньше плотности: она уточняет уже найденный
            # всплеск, а не находит его сама.
            active["chat_reactions"] = (cfg.chat_weight * 0.5, reaction_z)

        if not active:
            raise StageSkipped("все сигналы анализа выключены — искать моменты нечем")

        total = sum(weight for weight, _ in active.values()) or 1.0
        weights = {name: weight / total for name, (weight, _) in active.items()}
        ctx.log.info(
            "сигналы: %s",
            ", ".join(f"{name} {weights[name]:.2f}" for name in sorted(weights)),
        )

        score = sum(weights[name] * values for name, (_, values) in active.items())

        peaks = find_peaks(
            score,
            min_score=cfg.min_peak_score,
            min_gap=max(int(cfg.min_gap_seconds / cfg.window_seconds), 1),
        )
        ctx.log.info("окон %d, пиков выше порога %d", track.count, len(peaks))

        raw: list[dict] = []
        for index in peaks:
            candidate = self._build(
                index, track, score, loudness_z, density, chat, segments, short, cfg
            )
            if candidate is not None:
                raw.append(candidate)

        deduped = deduplicate(raw, max_overlap=funnel.dedup_overlap)
        total_seconds = float(transcript.get("duration_seconds") or track.count * cfg.window_seconds)
        kept, hit_ceiling = limit_coverage(
            deduped, total_seconds=total_seconds, max_coverage=cfg.max_coverage
        )
        kept = kept[: funnel.max_candidates]

        covered = sum(c["end"] - c["start"] for c in kept)
        share = covered / max(total_seconds, 1.0)

        Artifact(ctx.paths.analysis / CANDIDATES_NAME).write_json(
            {
                "source": "loudness+density+chat" if chat_z is not None else "loudness+density",
                "weights": weights,
                "stage_version": self.version,
                "window_seconds": cfg.window_seconds,
                "stats": {
                    "windows": track.count,
                    "peaks": len(peaks),
                    "built": len(raw),
                    "after_dedup": len(deduped),
                    "kept": len(kept),
                    "coverage": round(share, 3),
                    "hit_coverage_ceiling": hit_ceiling,
                    **chat_stats,
                },
                "candidates": kept,
            }
        )

        if not kept:
            ctx.log.warning("кандидатов не найдено — попробуйте снизить min_peak_score")
            return

        ctx.log.info(
            "кандидатов %d, суммарно %.0f с (%.0f%% материала)", len(kept), covered, share * 100
        )
        if hit_ceiling:
            ctx.log.warning(
                "упёрлись в потолок покрытия: всплески слабо отличаются от фона. "
                "Для однородной записи это ожидаемо, для стрима — повод проверить звук"
            )

    def _chat_signal(self, ctx: StageContext, window_count: int, cfg):
        """Всплеск чата и доля реакций. Возвращает (rate, z, reaction_z, stats)."""
        artifact = Artifact(ctx.paths.analysis / CHAT_NAME)
        if not artifact.exists():
            return None, None, None, {"chat": "нет"}

        try:
            messages = artifact.read_json().get("messages", [])
        except ValueError:
            ctx.log.warning("файл чата не читается — сигнал пропущен")
            return None, None, None, {"chat": "не читается"}

        if not messages:
            return None, None, None, {"chat": "пусто"}

        rate = chat_rate(
            messages, window_count, cfg.window_seconds,
            skip_bots=not cfg.chat_include_bots,
        )
        rate = forward_average(rate, max(int(cfg.chat_lead_seconds / cfg.window_seconds), 1))
        normalized = robust_z(rate)

        ignore = self._ignore_start(ctx)
        if ignore > 0:
            # Обнуляем именно нормализованный сигнал, а не сами сообщения:
            # приветствия должны перестать влиять на оценку, но не сдвигать
            # медиану, по которой считается всплеск на остальной записи.
            windows = min(int(ignore / cfg.window_seconds), normalized.size)
            normalized[:windows] = 0.0
            ctx.log.info("приветствия в первых %.0f с не учитываются", ignore)

        # Доля реакций считается по тем же окнам и глушится в том же начале:
        # приветствие «привет всем» тоже похоже на волну повторов.
        share = chat_reaction_share(
            messages, window_count, cfg.window_seconds,
            skip_bots=not cfg.chat_include_bots,
        )
        reaction_z = robust_z(share)
        if ignore > 0:
            reaction_z[: min(int(ignore / cfg.window_seconds), reaction_z.size)] = 0.0

        ctx.log.info(
            "чат: %d сообщений, в среднем %.2f в секунду, реакций %.0f%%",
            len(messages), float(rate.mean()), float(share.mean() * 100),
        )
        return rate, normalized, reaction_z, {
            "chat_messages": len(messages),
            "chat_ignored_start": ignore,
            "chat_reaction_share": round(float(share.mean()), 3),
        }

    def _build(
        self,
        index: int,
        track,
        score: np.ndarray,
        loudness_z: np.ndarray,
        density: np.ndarray,
        chat,
        segments: list[dict],
        short,
        cfg,
    ) -> dict | None:
        """Строит кандидата вокруг пика, выравнивая границы по фразам."""
        peak_time = track.time_of(index)
        half = short.optimal_duration / 2.0

        snapped = snap_to_segments(peak_time - half, peak_time + half, segments)
        if snapped is None:
            return None
        start, end = snapped

        duration = end - start
        if duration < short.min_duration or duration > short.max_duration:
            # Слишком коротко или слишком длинно после выравнивания:
            # окончательные границы всё равно уточнит LLM (§14, §15),
            # но заведомо непригодные окна дальше не тащим.
            if duration > short.max_duration:
                end = start + short.max_duration
                snapped = snap_to_segments(start, end, segments)
                if snapped is None:
                    return None
                start, end = snapped
                if end - start > short.max_duration * 1.5:
                    return None
            else:
                return None

        window = slice(index, index + 1)
        text = " ".join(
            s["text"] for s in segments if s["end"] > start and s["start"] < end
        ).strip()

        return {
            "start": round(start, 2),
            "end": round(end, 2),
            "peak_at": round(peak_time, 2),
            "provisional_score": round(float(score[index]), 3),
            "signals": {
                "loudness_z": round(float(loudness_z[window][0]), 3),
                "words_per_second": round(float(density[window][0]), 2),
                "rms_db": round(float(track.rms_db[window][0]), 1),
                **(
                    {"chat_rate_peak": round(float(chat[window][0]), 2)}
                    if chat is not None
                    else {}
                ),
            },
            "text": text,
        }
