"""Стадия extract_audio: звуковая дорожка для распознавания.

BAZA.md §6. 16 кГц моно — то, что ожидают модели распознавания. Исходная
дорожка остаётся нетронутой в видеофайле: она понадобится при сборке клипа,
где нужен полный звук, а не подготовленный для STT.
"""

from __future__ import annotations

from typing import Any

from narezka.core.artifacts import Artifact
from narezka.core.media import MediaError, find_source, run_tool
from narezka.core.stage import Device, Stage, StageContext

#: 8 часов 16 кГц моно PCM это около 0.9 ГБ — приемлемо (§65).
AUDIO_NAME = "audio.wav"


class ExtractAudioStage(Stage):
    name = "extract_audio"
    version = 1
    device = Device.ANY
    description = "Звуковая дорожка 16 кГц моно для распознавания"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        # Только ошибка поиска исходника: отказ диска или прав — не то же
        # самое, что «файла ещё нет», и одним ответом их путать нельзя.
        try:
            return [Artifact(find_source(ctx.paths.source))]
        except MediaError:
            return []

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.audio / AUDIO_NAME)]

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        return ctx.config.audio.model_dump()

    def check_available(self, ctx: StageContext) -> str | None:
        metadata = Artifact(ctx.paths.metadata)
        if metadata.exists():
            data = metadata.read_json()
            # has_audio появляется после probe; до неё проверять нечего.
            if data.get("has_audio") is False:
                return "в исходном видео нет звуковой дорожки"
        return None

    def run(self, ctx: StageContext) -> None:
        source = find_source(ctx.paths.source)
        cfg = ctx.config.audio
        target = Artifact(ctx.paths.audio / AUDIO_NAME)

        ctx.log.info("извлекаю звук: %d Гц, каналов %d", cfg.sample_rate, cfg.channels)

        with target.reserve() as tmp:
            run_tool(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v", "error",
                    "-y",
                    "-i", str(source),
                    "-vn",                          # видео не нужно
                    "-map", "0:a:0",                # первая звуковая дорожка
                    "-ac", str(cfg.channels),
                    "-ar", str(cfg.sample_rate),
                    "-c:a", "pcm_s16le",
                    "-f", "wav",
                    str(tmp),
                ],
                # 8-часовой VOD извлекается минуты, но запас нужен на медленный диск.
                timeout=3600,
            )

        size_mb = target.path.stat().st_size / 1024**2
        ctx.log.info("готово: %s (%.1f МБ)", AUDIO_NAME, size_mb)
