"""Стадия render: готовые вертикальные ролики.

BAZA.md §37 и §59. Нарезка идёт с перекодированием, а не копированием потока:
копирование режет по ключевым кадрам с ошибкой в несколько секунд, что для
клипа неприемлемо.

Кадрирование настраивается ([§61](BAZA.md#61)) — см. narezka/core/framing.py.
По умолчанию часть кадра отрезается по бокам, остальное вписывается по ширине
на размытую подложку. Это не статичный центральный кроп из §39: величина
обрезки задаётся пользователем и может быть нулевой, а точка привязки
выбирается. Автоматическое слежение за объектом появится на этапе 4.
"""

from __future__ import annotations

from typing import Any

from narezka.core.artifacts import Artifact
from narezka.core.config import FramingConfig
from narezka.core.fonts import escape_for_filter, fonts_dir
from narezka.core.framing import (
    Framing,
    build_filter,
    build_split_filter,
    describe,
    plan_frame,
    plan_split,
)
from narezka.core.media import find_source, run_tool
from narezka.core.stage import Device, Stage, StageContext, StageSkipped
from narezka.core.clips import SELECTION_NAME, describe_source, load_clips
from narezka.stages.subtitles import INDEX_NAME

SHORTS_INDEX = "index.json"

#: Если ffprobe не отдал размеры, считаем исходник обычным 16:9 — при этом
#: пресеты дают предсказуемый результат, а не падение.
FALLBACK_SIZE = (1920, 1080)


def load_options(ctx: StageContext) -> dict[str, bool]:
    """Что вшивать в ролик: конфиг, поверх него — правка для этого видео."""
    out = ctx.config.output
    options = {
        "subtitles_enabled": out.subtitles_enabled,
        "loudnorm_enabled": out.loudnorm_enabled,
    }
    override = Artifact(ctx.paths.framing)
    if override.exists():
        try:
            stored = override.read_json()
        except ValueError:
            return options
        if isinstance(stored, dict):
            for key in options:
                if isinstance(stored.get(key), bool):
                    options[key] = stored[key]
    return options


def load_framing(ctx: StageContext) -> Framing:
    """Настройки кадрирования: конфиг, поверх него — ручная правка для видео.

    Правка хранится отдельным файлом, а не в общем конфиге, потому что
    подходящее кадрирование зависит от конкретной записи: у стрима с вебкой
    в углу и у записи экрана оно разное.
    """
    data = ctx.config.output.framing.model_dump()

    override = Artifact(ctx.paths.framing)
    if override.exists():
        try:
            stored = override.read_json()
        except ValueError:
            stored = {}
        if isinstance(stored, dict):
            data.update({key: value for key, value in stored.items() if key in data})

    # Через pydantic — чтобы правка, пришедшая из файла или по API, проходила
    # ту же проверку диапазонов, что и конфиг.
    return Framing(**FramingConfig(**data).model_dump())


def source_size(metadata: dict[str, Any]) -> tuple[int, int]:
    video = metadata.get("video") or {}
    width, height = video.get("width"), video.get("height")
    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        return width, height
    return FALLBACK_SIZE


class RenderStage(Stage):
    name = "render"
    #: v7 — нижняя полоса сплита режется по найденной области контента.
    version = 7
    device = Device.ANY
    description = "Вертикальные ролики 9:16 с вшитыми субтитрами"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        selection = Artifact(ctx.paths.analysis / SELECTION_NAME)
        return [
            selection if selection.exists() else Artifact(ctx.paths.analysis / "candidates.json"),
            Artifact(ctx.paths.base / "subtitles" / INDEX_NAME),
        ]

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        index = Artifact(ctx.paths.shorts / SHORTS_INDEX)
        artifacts = [index]
        if index.exists():
            try:
                for entry in index.read_json().get("files", []):
                    artifacts.append(Artifact(ctx.paths.shorts / entry["file"]))
            except (ValueError, KeyError, TypeError):
                pass
        return artifacts

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        # Ручная правка кадрирования входит в ключ кэша наравне с конфигом:
        # изменил настройку — ролики перерендерятся, не изменил — нет.
        return {
            **ctx.config.output.model_dump(),
            "framing": load_framing(ctx).__dict__,
            **load_options(ctx),
            "subtitle_style": ctx.config.subtitles.style,
        }

    def run(self, ctx: StageContext) -> None:
        out = ctx.config.output
        short = out.short

        clips, clip_source = load_clips(ctx.paths)
        subtitle_index = Artifact(ctx.paths.base / "subtitles" / INDEX_NAME).read_json()
        subtitle_files = {entry["index"]: entry["file"] for entry in subtitle_index.get("files", [])}
        if not subtitle_files:
            raise StageSkipped("нет файлов субтитров")

        source = find_source(ctx.paths.source)
        metadata_artifact = Artifact(ctx.paths.metadata)
        metadata = metadata_artifact.read_json() if metadata_artifact.exists() else {}
        has_video = bool(metadata.get("has_video", True))

        framing = load_framing(ctx)
        options = load_options(ctx)
        src_w, src_h = source_size(metadata)
        plan = plan_frame(src_w, src_h, short.width, short.height, framing)

        if has_video:
            ctx.log.info(
                "кадрирование «%s»: %s (из %dx%d)",
                framing.preset, describe(plan), src_w, src_h,
            )
        else:
            ctx.log.warning(
                "в источнике нет картинки — ролик собирается на однотонном фоне. "
                "Кадрирование проверяется только на видео"
            )

        subtitles_dir = ctx.paths.base / "subtitles"
        ctx.paths.shorts.mkdir(parents=True, exist_ok=True)

        ctx.log.info(describe_source(clip_source, len(clips)))
        cams = self._facecams(ctx)

        rendered: list[dict[str, Any]] = []
        for clip in clips:
            index = clip["index"]
            name = subtitle_files.get(index) if options["subtitles_enabled"] else None
            if options["subtitles_enabled"] and name is None:
                continue

            duration = clip["end"] - clip["start"]
            target = Artifact(ctx.paths.shorts / f"{index:02d}.mp4")
            ctx.log.info("рендер %d: %.1f с", index, duration)
            ctx.progress(len(rendered) + 1, len(clips), "рендер")

            split = self._split_for(clip, cams, framing, src_w, src_h, short)

            with target.reserve() as tmp:
                run_tool(
                    self._command(
                        split=split,
                        source=source,
                        start=clip["start"],
                        duration=duration,
                        subtitle_name=name,
                        loudnorm=options["loudnorm_enabled"],
                        output=tmp,
                        has_video=has_video,
                        framing=framing,
                        plan=plan,
                        width=short.width,
                        height=short.height,
                        crf=out.crf,
                        fps=out.fps,
                        pix_fmt=out.pix_fmt,
                        faststart=out.faststart,
                        lufs=out.loudness_target_lufs,
                    ),
                    # Минута ролика на CPU — это единицы минут кодирования.
                    timeout=1800,
                    cwd=subtitles_dir,
                )

            rendered.append(
                {
                    "index": index,
                    "file": target.path.name,
                    "start": clip["start"],
                    "end": clip["end"],
                    "duration": round(duration, 2),
                    "size_bytes": target.path.stat().st_size,
                    # Оценка и объяснение доезжают до интерфейса вместе
                    # с роликом — иначе непонятно, почему он здесь.
                    "interest_score": clip.get("interest_score"),
                    "rank": clip.get("rank"),
                    "explanation": clip.get("explanation"),
                }
            )

        if not rendered:
            raise StageSkipped("ни один кандидат не отрендерен")

        Artifact(ctx.paths.shorts / SHORTS_INDEX).write_json(
            {
                "width": short.width,
                "height": short.height,
                "framing": {
                    **framing.__dict__,
                    "content_share": round(plan.content_share, 4),
                    "lost_share": round(plan.lost_share, 4),
                    "full_bleed": plan.full_bleed,
                    "summary": describe(plan),
                }
                if has_video
                else None,
                "background": self._background_kind(framing, plan, has_video),
                "source": clip_source,
                "files": rendered,
            }
        )
        total_mb = sum(f["size_bytes"] for f in rendered) / 1024**2
        ctx.log.info("готово роликов %d, суммарно %.1f МБ", len(rendered), total_mb)

    @staticmethod
    @staticmethod
    def _facecams(ctx: StageContext) -> dict[str, Any]:
        artifact = Artifact(ctx.paths.analysis / "facecam.json")
        if not artifact.exists():
            return {}
        try:
            return artifact.read_json().get("clips", {})
        except ValueError:
            return {}

    @staticmethod
    def _split_for(clip, cams, framing, src_w, src_h, short):
        """Раскладка сплита для клипа — если она вообще применима.

        Полноэкранная камера сплита не требует: делить кадр, где и так одно
        лицо, значит показать его дважды.
        """
        if framing.layout != "split":
            return None
        cam = cams.get(str(clip["index"]))
        if not cam or cam.get("full_frame"):
            return None
        face = cam.get("face") or {}
        content = cam.get("content") or {}
        return plan_split(
            src_w, src_h, short.width, short.height,
            (cam["x"], cam["y"], cam["width"], cam["height"]),
            face=(face["x"], face["y"], face["width"], face["height"]) if face else None,
            content=(
                (content["x"], content["y"], content["width"], content["height"])
                if content else None
            ),
            anchor=framing.anchor,
        )

    @staticmethod
    def _background_kind(framing: Framing, plan, has_video: bool) -> str:
        if not has_video:
            return "solid"
        if plan.full_bleed:
            return "none"
        if framing.background == "color":
            return "solid"
        return "blur" if framing.blur_sigma > 0 else "sharp"

    def _command(
        self,
        *,
        source,
        start: float,
        duration: float,
        subtitle_name: str | None,
        loudnorm: bool = True,
        output=None,
        has_video: bool,
        split=None,
        framing: Framing,
        plan,
        width: int,
        height: int,
        crf: int,
        fps: int | None = None,
        pix_fmt: str,
        faststart: bool,
        lufs: float,
    ) -> list[str]:
        """Собирает вызов ffmpeg.

        `-ss` стоит до `-i` — это быстрая перемотка по контейнеру; точность
        обеспечивается перекодированием.
        """
        args = ["ffmpeg", "-nostdin", "-v", "error", "-y"]

        # Пути абсолютные: ffmpeg запускается из каталога субтитров, потому что
        # экранирование пути внутри строки фильтра libass слишком хрупкое.
        source = source.resolve()
        output = output.resolve()

        if has_video:
            args += ["-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}"]
            if split is not None:
                video_filter = build_split_filter(
                    split, width, subtitle_name,
                    fonts_dir=escape_for_filter(fonts_dir()), fps=fps,
                )
            else:
                video_filter = build_filter(
                    plan, framing, width, height, subtitle_name,
                    fonts_dir=escape_for_filter(fonts_dir()), fps=fps,
                )
            args += ["-filter_complex", video_filter, "-map", "[v]", "-map", "0:a:0"]
        else:
            args += [
                "-f", "lavfi",
                "-i", f"color=c={framing.color}:s={width}x{height}:r=30:d={duration:.3f}",
                "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
                "-filter_complex",
                f"[0:v]subtitles={subtitle_name}:fontsdir={escape_for_filter(fonts_dir())}[v]",
                "-map", "[v]", "-map", "1:a:0",
            ]

        if loudnorm:
            args += ["-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11"]
        args += [
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", str(crf),
            "-pix_fmt", pix_fmt,          # без этого часть плееров файл не примет
            "-c:a", "aac",
            "-b:a", "160k",
            "-shortest",
            # Пишем во временный файл с расширением .partial (§58), поэтому
            # формат контейнера задаём явно — по имени ffmpeg его не угадает.
            "-f", "mp4",
        ]
        if faststart:
            args += ["-movflags", "+faststart"]
        args.append(str(output))
        return args
