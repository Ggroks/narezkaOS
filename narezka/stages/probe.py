"""Стадия probe: технические параметры исходного видео.

BAZA.md §6 — длительность, FPS, разрешение. Первая стадия пайплайна:
всё дальнейшее опирается на её результат.
"""

from __future__ import annotations

from typing import Any

from narezka.core.artifacts import Artifact
from narezka.core.media import MediaError, ffprobe, find_source, summarize
from narezka.core.stage import Device, Stage, StageContext


class ProbeStage(Stage):
    name = "probe"
    version = 1
    device = Device.ANY
    description = "Технические параметры исходного видео через ffprobe"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        # Исходник кладётся командой add или стадией download; в момент
        # объявления входов его может ещё не быть — тогда список пуст,
        # а точную причину назовёт запуск: он зовёт find_source сам.
        # Ловится только ошибка поиска: отказ диска или прав — это не
        # «исходника ещё нет», и прятать его под тем же ответом нельзя.
        try:
            return [Artifact(find_source(ctx.paths.source))]
        except MediaError:
            return []

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.metadata)]

    def run(self, ctx: StageContext) -> None:
        source = find_source(ctx.paths.source)
        ctx.log.info("читаю %s", source.name)

        summary: dict[str, Any] = summarize(ffprobe(source))
        summary["source_file"] = source.name

        existing: dict[str, Any] = {}
        artifact = Artifact(ctx.paths.metadata)
        if artifact.exists():
            try:
                existing = artifact.read_json()
            except ValueError:
                existing = {}
        # Данные о происхождении записала команда add — их сохраняем.
        existing.update(summary)
        artifact.write_json(existing)

        duration = summary.get("duration_seconds")
        video = summary.get("video") or {}
        ctx.log.info(
            "%s, %sx%s @ %s fps",
            f"{duration:.0f} с" if duration else "длительность неизвестна",
            video.get("width", "?"),
            video.get("height", "?"),
            video.get("fps", "?"),
        )
