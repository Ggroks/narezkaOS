"""Стадия compilation: длинный горизонтальный ролик.

BAZA.md §20, §21. Два режима, и это разные продукты:

- **story** — один связный эпизод записи, уплотнённый до нужной длины.
  Сюжет не придумывается, он уже есть: раунд, просмотр видео, дорога;
- **best** — подборка лучших моментов со всей записи, расставленных по
  ролям: зацепка, середина в хронологии, кульминация, концовка.

Режим выбирает человек: лучшего из них не бывает, они отвечают на разные
запросы. Стадия необязательна — шортсы делаются и без неё.
"""

from __future__ import annotations

from typing import Any

from narezka.core import compilation as core
from narezka.core.artifacts import Artifact
from narezka.core.assemble import cut_piece, join_pieces, total_duration
from narezka.core.clips import load_clips
from narezka.core.media import find_source
from narezka.core.stage import Device, Stage, StageContext, StageSkipped

EPISODES_NAME = "episodes.json"
PLAN_NAME = "compilation.json"
OUTPUT_NAME = "compilation.mp4"


class CompilationStage(Stage):
    name = "compilation"
    #: v1 — сюжетный эпизод и подборка лучших моментов.
    version = 1
    #: Двадцать минут видео перекодируются дольше тридцати шортсов.
    timeout_seconds = 3600
    device = Device.ANY
    optional = True
    description = "Длинная компиляция: сюжетный эпизод или подборка лучших"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        inputs = [Artifact(ctx.paths.analysis / "selection.json")]
        episodes = Artifact(ctx.paths.analysis / EPISODES_NAME)
        if episodes.exists():
            inputs.append(episodes)
        return inputs

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [
            Artifact(ctx.paths.analysis / PLAN_NAME),
            Artifact(ctx.paths.base / OUTPUT_NAME),
        ]

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        cfg = ctx.config.compilation
        return {
            "mode": cfg.mode,
            "target_minutes": cfg.target_minutes,
            "episode": cfg.episode,
            "width": ctx.config.output.compilation_width,
            "height": ctx.config.output.compilation_height,
        }

    def check_available(self, ctx: StageContext) -> str | None:
        if not ctx.config.compilation.enabled:
            return "компиляция выключена"
        return None

    def run(self, ctx: StageContext) -> None:
        cfg = ctx.config.compilation
        target = cfg.target_minutes * 60.0
        clips, source_name = load_clips(ctx.paths)
        if not clips:
            raise StageSkipped("нет отобранных моментов")

        if cfg.mode == "story":
            pieces, plan = self._story(ctx, clips, target)
        else:
            pieces, plan = self._best(ctx, clips, target)

        if not pieces:
            raise StageSkipped("собирать нечего")

        ctx.log.info(
            "режим %s: кусков %d, длительность %.1f мин",
            cfg.mode, len(pieces), total_duration(pieces) / 60,
        )
        Artifact(ctx.paths.analysis / PLAN_NAME).write_json({
            "mode": cfg.mode,
            "source": source_name,
            "duration": round(total_duration(pieces), 2),
            "pieces": [{"start": round(a, 2), "end": round(b, 2)} for a, b in pieces],
            **plan,
        })

        out = ctx.config.output
        source = find_source(ctx.paths.source).resolve()
        target_file = Artifact(ctx.paths.base / OUTPUT_NAME)

        # Куски режутся по одному: памяти нужно на один, сколько бы их ни было.
        # Склейка одним проходом на двадцати восьми кусках вразнобой съедала
        # всю память машины — ffmpeg держал раскодированное, пока склейка до
        # него не дойдёт.
        workdir = ctx.paths.base / "compilation.parts"
        workdir.mkdir(parents=True, exist_ok=True)
        made: list[Any] = []
        try:
            for number, (start, end) in enumerate(pieces):
                ctx.progress(number + 1, len(pieces) + 1, "нарезка кусков")
                part = workdir / f"{number:03d}.mp4"
                cut_piece(
                    source, start, end, part,
                    width=out.compilation_width, height=out.compilation_height,
                    crf=out.crf, pix_fmt=out.pix_fmt, fps=out.fps,
                )
                made.append(part)
                ctx.log.info(
                    "кусок %d из %d: %.0f–%.0f с", number + 1, len(pieces), start, end
                )

            ctx.progress(len(pieces) + 1, len(pieces) + 1, "склейка")
            with target_file.reserve() as tmp:
                join_pieces(made, workdir / "list.txt", tmp)
        finally:
            # Временные куски убираются всегда: двадцать файлов по сотне
            # мегабайт не должны переживать неудачную сборку.
            for path in workdir.glob("*"):
                path.unlink(missing_ok=True)
            workdir.rmdir()

        size_mb = target_file.path.stat().st_size / 1024**2
        ctx.log.info("готово: %s, %.0f МБ", OUTPUT_NAME, size_mb)

    def _story(self, ctx: StageContext, clips, target: float):
        """Сюжетный режим: выбранный эпизод, уплотнённый до нужной длины."""
        artifact = Artifact(ctx.paths.analysis / EPISODES_NAME)
        if not artifact.exists():
            raise StageSkipped(
                "эпизоды не размечены — запустите поиск эпизодов или выберите "
                "режим подборки лучших моментов"
            )
        episodes = artifact.read_json().get("episodes", [])
        if not episodes:
            raise StageSkipped("связных эпизодов в записи не нашлось")

        chosen = ctx.config.compilation.episode
        if chosen is not None and 0 <= chosen < len(episodes):
            episode = episodes[chosen]
        else:
            # Без явного выбора берётся самый цельный и достаточно длинный:
            # короткий цельный эпизод не растянуть до двадцати минут, а
            # длинный бессвязный не станет рассказом.
            episode = max(episodes, key=lambda e: e["coherence"] * e["duration"])

        ctx.log.info(
            "эпизод «%s», %.0f–%.0f мин, цельность %.2f",
            episode["title"], episode["start"] / 60, episode["end"] / 60,
            episode["coherence"],
        )
        keep = [
            (c["start"], c["end"]) for c in clips
            if c["start"] < episode["end"] and c["end"] > episode["start"]
        ]
        pieces = core.condense(episode["start"], episode["end"], keep, target)
        return pieces, {"episode": episode, "highlights_inside": len(keep)}

    def _best(self, ctx: StageContext, clips, target: float):
        """Подборка лучших: моменты со всей записи, расставленные по ролям."""
        result = core.arrange(clips, target)
        pieces = [(p.clip["start"], p.clip["end"]) for p in result.parts]
        for part in result.parts:
            ctx.log.info(
                "  %-8s #%s оценка %s",
                part.role, part.clip.get("index"), part.clip.get("interest_score"),
            )
        return pieces, {"arrangement": result.as_dict()}
