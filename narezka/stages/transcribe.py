"""Стадия transcribe: распознавание речи с потайминговкой слов.

BAZA.md §7. Word-level timestamps — основа субтитров (§18), поиска границ
клипа (§14, §15) и удаления тишины (§16), поэтому запрашиваются всегда,
а не по требованию.

Результат проходит фильтр галлюцинаций (§57) перед записью.
"""

from __future__ import annotations

from typing import Any

from narezka.core.artifacts import Artifact
from narezka.core.stage import Device, Stage, StageContext, StageSkipped
from narezka.core.transcript import mark_suspect_segments
from narezka.stages.extract_audio import AUDIO_NAME

TRANSCRIPT_NAME = "transcript.json"

#: Соответствие устройства и типа вычислений, когда в конфиге стоит auto.
COMPUTE_TYPE_BY_DEVICE = {"cpu": "int8", "cuda": "float16"}

#: Длина куска, которым читается длинная запись. Замер: пятичасовая запись,
#: отданная faster-whisper целиком, выедает всю память машины и подвешивает
#: её — Silero VAD и выравнивание слов держат весь звук распакованным сразу.
#: Полчаса звука это около 100 МБ в памяти, что безопасно на любой машине.
CHUNK_SECONDS = 1800

#: Перекрытие кусков. Реплика на стыке иначе рвётся пополам и распознаётся
#: как два обрывка.
CHUNK_OVERLAP = 5.0

#: Дефолты Silero VAD. speech_pad_ms важен: без запаса речь обрезается
#: на границах (заимствовано из upstream, см. docs/upstream-notes.md).
VAD_PARAMETERS = {
    "threshold": 0.5,
    "min_speech_duration_ms": 250,
    "min_silence_duration_ms": 700,
    "speech_pad_ms": 400,
}


class TranscribeStage(Stage):
    name = "transcribe"
    #: v5 — фильтр перенастроен по замерам на записи стрима: no_speech_prob
    #: не отбраковывает в одиночку, петля определяется долей сегмента,
    #: а не счётчиком повторов (§57).
    version = 5
    device = Device.ANY
    description = "Распознавание речи с потайминговкой отдельных слов"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.audio / AUDIO_NAME)]

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.transcript / TRANSCRIPT_NAME)]

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        # Устройство влияет на compute_type, а тот — на результат,
        # поэтому входит в ключ кэша.
        return {**ctx.config.stt.model_dump(), "device": ctx.device.kind}

    def _compute_type(self, ctx: StageContext) -> str:
        configured = ctx.config.stt.compute_type
        if configured != "auto":
            return configured
        return COMPUTE_TYPE_BY_DEVICE.get(ctx.device.kind, "int8")

    def run(self, ctx: StageContext) -> None:
        from faster_whisper import WhisperModel  # noqa: PLC0415 — тяжёлый импорт

        cfg = ctx.config.stt
        audio = ctx.paths.audio / AUDIO_NAME
        compute_type = self._compute_type(ctx)

        ctx.log.info(
            "модель %s, устройство %s, тип вычислений %s",
            cfg.model, ctx.device.kind, compute_type,
        )
        if ctx.device.kind == "cpu" and cfg.model.startswith("large"):
            ctx.log.warning(
                "large на CPU идёт медленнее реального времени — для разработки "
                "используйте профиль dev (§62)"
            )

        try:
            model = WhisperModel(cfg.model, device=ctx.device.kind, compute_type=compute_type)
        except Exception as exc:  # noqa: BLE001
            raise StageSkipped(f"не удалось загрузить модель {cfg.model}: {exc}") from exc

        language = None if cfg.language == "auto" else cfg.language

        import numpy as np  # noqa: PLC0415
        from narezka.core.signals import read_wav_mono  # noqa: PLC0415

        samples, rate = read_wav_mono(audio)
        total_seconds = len(samples) / rate

        # Длинная запись читается кусками: отданная целиком, она выедает всю
        # память — Silero VAD и выравнивание слов держат весь звук
        # распакованным сразу. На пятичасовой записи это подвесило машину.
        chunk_samples = int(CHUNK_SECONDS * rate)
        overlap_samples = int(CHUNK_OVERLAP * rate)
        starts = list(range(0, len(samples), chunk_samples)) or [0]

        segments: list[dict[str, Any]] = []
        info = None
        next_id = 0
        last_end = 0.0

        for number, begin in enumerate(starts, start=1):
            finish = min(begin + chunk_samples + overlap_samples, len(samples))
            offset = begin / rate
            if len(starts) > 1:
                ctx.log.info(
                    "кусок %d из %d: %.0f–%.0f с из %.0f",
                    number, len(starts), offset, finish / rate, total_seconds,
                )
                ctx.progress(number, len(starts), "расшифровка")

            piece = samples[begin:finish].astype(np.float32)
            segment_iter, chunk_info = model.transcribe(
                piece,
                language=language or (info.language if info else None),
                word_timestamps=True,
                vad_filter=cfg.vad_filter,
                vad_parameters=VAD_PARAMETERS if cfg.vad_filter else None,
            )
            if info is None:
                info = chunk_info

            for segment in segment_iter:
                start = segment.start + offset
                # Перекрытие кусков даёт повтор на стыке — берём только то,
                # что начинается позже уже разобранного.
                if start < last_end - 0.05:
                    continue
                last_end = max(last_end, segment.end + offset)
                segments.append(
                    self._segment_dict(segment, offset, next_id)
                )
                next_id += 1

            del piece

        if info is None:
            raise StageSkipped("звук не удалось разобрать")

        del samples

        mark_suspect_segments(
            segments,
            max_no_speech_prob=cfg.max_no_speech_prob,
            min_avg_logprob=cfg.min_avg_logprob,
            max_repeat_ratio=cfg.max_repeat_ratio,
        )

        suspect_count = sum(1 for s in segments if s["suspect"])
        word_count = sum(len(s["words"]) for s in segments if not s["suspect"])

        result = {
            "language": info.language,
            "language_probability": round(info.language_probability, 4),
            "duration_seconds": round(total_seconds, 3),
            "model": cfg.model,
            "compute_type": compute_type,
            "device": ctx.device.kind,
            "vad_filter": cfg.vad_filter,
            "chunks": len(starts),
            "segments": segments,
            "stats": {
                "segments_total": len(segments),
                "segments_suspect": suspect_count,
                "words_usable": word_count,
            },
        }
        Artifact(ctx.paths.transcript / TRANSCRIPT_NAME).write_json(result)

        ctx.log.info(
            "язык %s (%.2f), сегментов %d, из них подозрительных %d, слов %d",
            info.language, info.language_probability, len(segments), suspect_count, word_count,
        )
        if segments and suspect_count == len(segments):
            ctx.log.warning(
                "все сегменты помечены подозрительными — вероятно, в записи нет речи"
            )

    @staticmethod
    def _segment_dict(segment, offset: float, segment_id: int) -> dict[str, Any]:
        """Сегмент со сдвигом на начало куска: метки времени должны быть
        от начала записи, а не от начала куска."""
        return {
            "id": segment_id,
            "start": round(segment.start + offset, 3),
            "end": round(segment.end + offset, 3),
            "text": segment.text.strip(),
            "avg_logprob": round(segment.avg_logprob, 4),
            "no_speech_prob": round(segment.no_speech_prob, 4),
            "compression_ratio": round(segment.compression_ratio, 4),
            "words": [
                {
                    "word": word.word.strip(),
                    "start": round(word.start + offset, 3),
                    "end": round(word.end + offset, 3),
                    "probability": round(word.probability, 4),
                }
                for word in (segment.words or [])
            ],
        }
