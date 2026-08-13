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
    #: v2 — уточнён критерий зацикливания: эмоциональный повтор в живой речи
    #: больше не считается галлюцинацией (§57).
    version = 2
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
        segment_iter, info = model.transcribe(
            str(audio),
            language=language,
            word_timestamps=True,
            vad_filter=cfg.vad_filter,
            vad_parameters=VAD_PARAMETERS if cfg.vad_filter else None,
        )

        segments: list[dict[str, Any]] = []
        for segment in segment_iter:
            segments.append(
                {
                    "id": segment.id,
                    "start": round(segment.start, 3),
                    "end": round(segment.end, 3),
                    "text": segment.text.strip(),
                    "avg_logprob": round(segment.avg_logprob, 4),
                    "no_speech_prob": round(segment.no_speech_prob, 4),
                    "compression_ratio": round(segment.compression_ratio, 4),
                    "words": [
                        {
                            "word": word.word.strip(),
                            "start": round(word.start, 3),
                            "end": round(word.end, 3),
                            "probability": round(word.probability, 4),
                        }
                        for word in (segment.words or [])
                    ],
                }
            )

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
            "duration_seconds": round(info.duration, 3),
            "model": cfg.model,
            "compute_type": compute_type,
            "device": ctx.device.kind,
            "vad_filter": cfg.vad_filter,
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
