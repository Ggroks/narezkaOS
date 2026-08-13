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
from narezka.core.subtitles import STYLES, build_ass, words_in_range
from narezka.stages.candidates import CANDIDATES_NAME
from narezka.stages.transcribe import TRANSCRIPT_NAME

INDEX_NAME = "index.json"


class SubtitlesStage(Stage):
    name = "subtitles"
    version = 1
    device = Device.ANY
    description = "Файлы ASS с подсветкой слова для каждого кандидата"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        return [
            Artifact(ctx.paths.transcript / TRANSCRIPT_NAME),
            Artifact(ctx.paths.analysis / CANDIDATES_NAME),
        ]

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
        return {
            "style": ctx.config.subtitles.style,
            "width": ctx.config.output.short.width,
            "height": ctx.config.output.short.height,
        }

    def run(self, ctx: StageContext) -> None:
        style_name = ctx.config.subtitles.style
        style = STYLES.get(style_name)
        if style is None:
            raise StageSkipped(f"неизвестный стиль субтитров '{style_name}'; доступны: {', '.join(STYLES)}")

        transcript = Artifact(ctx.paths.transcript / TRANSCRIPT_NAME).read_json()
        candidates = Artifact(ctx.paths.analysis / CANDIDATES_NAME).read_json().get("candidates", [])
        if not candidates:
            raise StageSkipped("нет кандидатов — субтитры не для чего собирать")

        segments = transcript.get("segments", [])
        short = ctx.config.output.short
        directory = ctx.paths.base / "subtitles"
        directory.mkdir(parents=True, exist_ok=True)

        files: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            words = words_in_range(segments, candidate["start"], candidate["end"])
            if not words:
                ctx.log.warning("кандидат %d без слов, пропускаю", index)
                continue

            ass = build_ass(
                words,
                style=style,
                width=short.width,
                height=short.height,
                time_offset=candidate["start"],
            )
            name = f"{index:02d}.ass"
            Artifact(directory / name).write_bytes(ass.encode("utf-8"))
            files.append(
                {
                    "index": index,
                    "file": name,
                    "start": candidate["start"],
                    "end": candidate["end"],
                    "words": len(words),
                }
            )

        if not files:
            raise StageSkipped("ни у одного кандидата не нашлось слов в границах")

        Artifact(directory / INDEX_NAME).write_json(
            {"style": style_name, "width": short.width, "height": short.height, "files": files}
        )
        ctx.log.info(
            "субтитры для %d кандидатов, стиль %s, слов всего %d",
            len(files), style_name, sum(f["words"] for f in files),
        )
