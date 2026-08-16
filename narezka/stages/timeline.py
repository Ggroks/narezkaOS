"""Стадия timeline: список правок и пересчёт времени.

BAZA.md §16, §44. Связывает три чистых модуля с пайплайном: `edl` считает
пересчёт, `silence` находит вырезаемые паузы, `remap` переносит транскрипт
и клипы в выходное время.

Стадия **необязательна и по умолчанию выключена**. Удаление пауз заметно
меняет готовый ролик, и включать его молча за пользователя нельзя: у кого-то
паузы и есть манера речи. Пока выключено, стадия сохраняет тождественную ось
времени — весь код ниже по течению работает одинаково, с правками и без них.
"""

from __future__ import annotations

from typing import Any

from narezka.core.artifacts import Artifact
from narezka.core.clips import load_clips
from narezka.core.edl import Edl
from narezka.core.remap import remap_clips, remap_segments, verify
from narezka.core.signals import loudness_track, read_wav_mono
from narezka.core.silence import plan_cuts
from narezka.core.stage import Device, Stage, StageContext

AUDIO_NAME = "audio.wav"
TRANSCRIPT_NAME = "transcript.json"
TIMELINE_NAME = "timeline.json"

#: Окно измерения громкости для поиска пауз. Меньше, чем у поиска моментов:
#: там оценивается всплеск за десяток секунд, здесь — граница тишины, и
#: секундное окно размазало бы её на полсекунды в каждую сторону.
WINDOW_SECONDS = 0.25


class TimelineStage(Stage):
    name = "timeline"
    #: v1 — ось времени и удаление пауз.
    version = 1
    device = Device.ANY
    optional = True
    description = "Ось времени: удаление пауз и пересчёт субтитров"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        return [
            Artifact(ctx.paths.audio / AUDIO_NAME),
            Artifact(ctx.paths.transcript / TRANSCRIPT_NAME),
        ]

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.analysis / TIMELINE_NAME)]

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        cfg = ctx.config.timeline
        return {
            "remove_silence": cfg.remove_silence,
            "min_pause_seconds": cfg.min_pause_seconds,
            "keep_pause_seconds": cfg.keep_pause_seconds,
            "keep_before_speech_seconds": cfg.keep_before_speech_seconds,
            "silence_drop_db": cfg.silence_drop_db,
        }

    def run(self, ctx: StageContext) -> None:
        cfg = ctx.config.timeline
        artifact = Artifact(ctx.paths.analysis / TIMELINE_NAME)

        if not cfg.remove_silence:
            # Не пропуск стадии, а её осознанный результат: тождественная ось
            # означает «правок нет», и весь код ниже пользуется ей так же,
            # как настоящей. Пропустив стадию, мы заставили бы каждого
            # потребителя проверять наличие артефакта.
            ctx.log.info("удаление пауз выключено — ось времени тождественна")
            artifact.write_json({
                "enabled": False,
                "found": 0, "cut": 0, "removed_seconds": 0.0,
                "edl": Edl.identity().as_dict(),
            })
            return

        samples, rate = read_wav_mono(ctx.paths.audio / AUDIO_NAME)
        duration = len(samples) / rate if rate else 0.0
        track = loudness_track(samples, rate, WINDOW_SECONDS)

        segments = Artifact(ctx.paths.transcript / TRANSCRIPT_NAME).read_json().get("segments", [])
        words = [word for segment in segments for word in segment.get("words", [])]

        report = plan_cuts(
            track.rms_db, WINDOW_SECONDS, duration, words,
            drop_db=cfg.silence_drop_db,
            min_pause=cfg.min_pause_seconds,
            keep_pause=cfg.keep_pause_seconds,
            keep_before_speech=cfg.keep_before_speech_seconds,
        )
        ctx.log.info(
            "пауз найдено %d, вырезано %d, сохранено %d — убрано %.0f с (%.1f%%)",
            report.found, report.cut, report.found - report.cut,
            report.removed_seconds,
            report.removed_seconds / duration * 100 if duration else 0.0,
        )

        moved_segments = remap_segments(segments, report.edl)
        moved_words = [w for s in moved_segments for w in s.get("words", [])]

        # Проверка на своих же данных, а не только в тестах: рассинхрон
        # субтитров виден зрителю, а не разработчику, и ловить его надо здесь.
        problems = verify(moved_words, report.edl)
        if problems:
            ctx.log.warning("расхождений после пересчёта: %d", len(problems))
            for problem in problems[:5]:
                ctx.log.warning("  %s", problem)
        else:
            ctx.log.info("пересчёт сходится: слов %d, расхождений нет", len(moved_words))

        try:
            clips, source = load_clips(ctx.paths)
        except (FileNotFoundError, ValueError):
            clips, source = [], "нет"
        moved_clips = remap_clips(clips, report.edl) if clips else []
        if clips:
            ctx.log.info(
                "клипов %d -> %d (%s)", len(clips), len(moved_clips), source
            )

        artifact.write_json({
            "enabled": True,
            **report.as_dict(),
            "source_duration": round(duration, 3),
            "words_before": len(words),
            "words_after": len(moved_words),
            "clips_before": len(clips),
            "clips_after": len(moved_clips),
            "mismatches": problems[:20],
        })
