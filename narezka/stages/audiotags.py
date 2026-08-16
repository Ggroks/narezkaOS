"""Стадия audiotags: разметка звука по классам AudioSet.

BAZA.md §10, §43. Считает дорожки тегов один раз на запись и сохраняет их —
дальше ими пользуются и отбор кандидатов, и оценка клипов, и обзор.

Стадия необязательна, и **каждый тег включается отдельно**: смех полезен
почти всем, музыка нужна тем, кто публикует ролики и рискует правами на неё,
а аплодисменты и толпа осмысленны лишь на записях с залом. Считать то, чем
не пользуются, значит платить временем и местом за ничто.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from narezka.core import audiotags
from narezka.core.artifacts import Artifact
from narezka.core.signals import read_wav_mono
from narezka.core.stage import Device, Stage, StageContext, StageSkipped

AUDIO_NAME = "audio.wav"
TAGS_NAME = "audiotags.json"

#: Кусок, которым запись подаётся в модель. Целиком нельзя: на пятичасовой
#: записи промежуточные активации занимают гигабайты, а именно на этом
#: пайплайн уже один раз выел всю память машины.
CHUNK_SECONDS = 600


class AudioTagsStage(Stage):
    name = "audiotags"
    #: v1 — теги AudioSet через YAMNet.
    version = 1
    device = Device.ANY
    optional = True
    description = "Теги звука: смех, крик, аплодисменты, музыка"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.audio / AUDIO_NAME)]

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.analysis / TAGS_NAME)]

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        cfg = ctx.config.audiotags
        return {
            "enabled": cfg.enabled,
            "tags": sorted(self._wanted(ctx)),
            "min_score": cfg.min_score,
        }

    @staticmethod
    def _wanted(ctx: StageContext) -> set[str]:
        """Какие теги считать. Пустое множество — считать нечего."""
        cfg = ctx.config.audiotags
        flags = {
            "laughter": cfg.laughter,
            "applause": cfg.applause,
            "shout": cfg.shout,
            "crowd": cfg.crowd,
            "music": cfg.music,
        }
        return {name for name, on in flags.items() if on}

    def check_available(self, ctx: StageContext) -> str | None:
        if not ctx.config.audiotags.enabled:
            return "разметка звука выключена"
        if not self._wanted(ctx):
            return "ни один тег не выбран"
        return audiotags.available()

    def run(self, ctx: StageContext) -> None:
        wanted = {
            name: indices
            for name, indices in audiotags.TAGS.items()
            if name in self._wanted(ctx)
        }
        if not wanted:
            raise StageSkipped("ни один тег не выбран")

        samples, rate = read_wav_mono(ctx.paths.audio / AUDIO_NAME)
        if samples.size == 0:
            raise StageSkipped("в записи нет звука")
        if rate != audiotags.SAMPLE_RATE:
            # Не пересчитываем молча: некачественная передискретизация
            # смещает спектр и портит распознавание, а звук у нас и так
            # готовится в нужной частоте.
            raise StageSkipped(
                f"звук {rate} Гц, а модель обучена на {audiotags.SAMPLE_RATE}"
            )

        tagger = audiotags.Yamnet()
        step = CHUNK_SECONDS * rate
        chunks = max(1, (samples.size + step - 1) // step)
        pieces: dict[str, list[np.ndarray]] = {}

        for number, begin in enumerate(range(0, samples.size, step), start=1):
            if chunks > 1:
                ctx.log.info("кусок %d из %d", number, chunks)
                ctx.progress(number, chunks, "разметка звука")
            track = tagger.tag(samples[begin : begin + step].astype(np.float32), wanted)
            for name, values in track.scores.items():
                pieces.setdefault(name, []).append(values)

        scores = {name: np.concatenate(values) for name, values in pieces.items()}
        threshold = ctx.config.audiotags.min_score
        summary = {
            name: {
                "peak": round(float(values.max()), 3) if values.size else 0.0,
                "seconds_above": round(
                    float((values >= threshold).sum() * audiotags.FRAME_HOP), 1
                ),
            }
            for name, values in scores.items()
        }
        for name, stats in sorted(summary.items()):
            ctx.log.info(
                "%s: пик %.2f, выше порога %.0f с", name, stats["peak"], stats["seconds_above"]
            )

        Artifact(ctx.paths.analysis / TAGS_NAME).write_json({
            "hop": audiotags.FRAME_HOP,
            "min_score": threshold,
            "summary": summary,
            # Дорожки округляются до сотых: третий знак вероятности ничего
            # не решает, а файл на пятичасовой записи вдвое легче.
            "scores": {
                name: [round(float(v), 2) for v in values]
                for name, values in scores.items()
            },
        })
