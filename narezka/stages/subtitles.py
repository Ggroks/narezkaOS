"""Стадия subtitles: файлы ASS для каждого кандидата.

BAZA.md §18 и §60. Один файл на кандидата, время отсчитывается от начала
клипа — именно так его ждёт рендер.

Субтитры не вжигаются здесь: в интерфейсе они рисуются поверх исходника,
а вжигание происходит только на экспорте (§69). Поэтому стадия быстрая
и её результат можно смотреть, не дожидаясь рендера.
"""

from __future__ import annotations

from typing import Any

from narezka.core.artifacts import Artifact
from narezka.core.stage import Device, Stage, StageContext, StageSkipped
from narezka.core.subtitles import build_ass, words_in_range
from narezka.core import settings
from narezka.core.clips import SELECTION_NAME, clip_inputs, describe_source, load_clips
from narezka.stages.transcribe import TRANSCRIPT_NAME

INDEX_NAME = "index.json"


class SubtitlesStage(Stage):
    name = "subtitles"
    group = "shorts"
    #: v3 — субтитры собираются по отобранным моделью клипам с уточнёнными
    #: границами, а не по сырым кандидатам (§11).
    version = 3
    device = Device.ANY
    description = "Файлы ASS с подсветкой слова для каждого клипа"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        # Отбор моделью необязателен: без него берутся кандидаты. Он указан
        # входом, чтобы появление selection.json пересобрало субтитры.
        return [Artifact(ctx.paths.transcript / TRANSCRIPT_NAME), *clip_inputs(ctx.paths)]

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        index = Artifact(ctx.paths.base / "subtitles" / INDEX_NAME)
        artifacts = [index]
        if index.exists():
            try:
                for entry in index.read_json().get("files", []):
                    artifacts.append(Artifact(ctx.paths.base / "subtitles" / entry["file"]))
            except (ValueError, KeyError, TypeError):
                pass
        return artifacts

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        # Весь стиль целиком, а не его имя: правка цвета или числа слов
        # в строке обязана пересобрать субтитры, иначе ролик остаётся
        # с прежними, и выглядит это как «настройка не работает».
        return {
            "style": settings.subtitles(ctx.config, ctx.paths).__dict__,
            "width": ctx.config.output.short.width,
            "height": ctx.config.output.short.height,
        }

    def run(self, ctx: StageContext) -> None:
        style = settings.subtitles(ctx.config, ctx.paths)

        transcript = Artifact(ctx.paths.transcript / TRANSCRIPT_NAME).read_json()
        clips, source = load_clips(ctx.paths)
        if not clips:
            raise StageSkipped("нет клипов — субтитры не для чего собирать")
        ctx.log.info(describe_source(source, len(clips)))

        segments = transcript.get("segments", [])
        short = ctx.config.output.short
        directory = ctx.paths.base / "subtitles"
        directory.mkdir(parents=True, exist_ok=True)

        files: list[dict[str, Any]] = []
        for clip in clips:
            index = clip["index"]
            words = words_in_range(segments, clip["start"], clip["end"])
            if not words:
                ctx.log.warning("клип %d без слов, пропускаю", index)
                continue

            ass = build_ass(
                words,
                style=style,
                width=short.width,
                height=short.height,
                time_offset=clip["start"],
            )
            name = f"{index:02d}.ass"
            Artifact(directory / name).write_bytes(ass.encode("utf-8"))
            files.append(
                {
                    "index": index,
                    "file": name,
                    "start": clip["start"],
                    "end": clip["end"],
                    "words": len(words),
                }
            )

        if not files:
            raise StageSkipped("ни у одного кандидата не нашлось слов в границах")

        Artifact(directory / INDEX_NAME).write_json(
            {"style": style.name, "width": short.width, "height": short.height, "files": files}
        )
        ctx.log.info(
            "субтитры для %d кандидатов, стиль %s, слов всего %d",
            len(files), style.name, sum(f["words"] for f in files),
        )
