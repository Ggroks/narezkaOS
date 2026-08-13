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
from narezka.core.framing import Framing, build_filter, describe, plan_frame
from narezka.core.media import find_source, run_tool
from narezka.core.stage import Device, Stage, StageContext, StageSkipped
from narezka.stages.candidates import CANDIDATES_NAME
from narezka.stages.subtitles import INDEX_NAME

SHORTS_INDEX = "index.json"

#: Если ffprobe не отдал размеры, считаем исходник обычным 16:9 — при этом
#: пресеты дают предсказуемый результат, а не падение.
FALLBACK_SIZE = (1920, 1080)


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
    #: v2 — кадрирование стало настраиваемым (§61).
    version = 2
    device = Device.ANY
    description = "Вертикальные ролики 9:16 с вшитыми субтитрами"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        return [
            Artifact(ctx.paths.analysis / CANDIDATES_NAME),
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
            "subtitle_style": ctx.config.subtitles.style,
        }

    def run(self, ctx: StageContext) -> None:
        out = ctx.config.output
        short = out.short

        candidates = Artifact(ctx.paths.analysis / CANDIDATES_NAME).read_json().get("candidates", [])
        subtitle_index = Artifact(ctx.paths.base / "subtitles" / INDEX_NAME).read_json()
        subtitle_files = {entry["index"]: entry["file"] for entry in subtitle_index.get("files", [])}
        if not subtitle_files:
            raise StageSkipped("нет файлов субтитров")

        source = find_source(ctx.paths.source)
        metadata_artifact = Artifact(ctx.paths.metadata)
        metadata = metadata_artifact.read_json() if metadata_artifact.exists() else {}
        has_video = bool(metadata.get("has_video", True))

        framing = load_framing(ctx)
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

        rendered: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            name = subtitle_files.get(index)
            if name is None:
                continue

            duration = candidate["end"] - candidate["start"]
            target = Artifact(ctx.paths.shorts / f"{index:02d}.mp4")
            ctx.log.info("рендер %d: %.1f с", index, duration)

            with target.reserve() as tmp:
                run_tool(
                    self._command(
                        source=source,
                        start=candidate["start"],
                        duration=duration,
                        subtitle_name=name,
                        output=tmp,
                        has_video=has_video,
                        framing=framing,
                        plan=plan,
                        width=short.width,
                        height=short.height,
                        crf=out.crf,
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
                    "start": candidate["start"],
                    "end": candidate["end"],
                    "duration": round(duration, 2),
                    "size_bytes": target.path.stat().st_size,
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
                "files": rendered,
            }
        )
        total_mb = sum(f["size_bytes"] for f in rendered) / 1024**2
        ctx.log.info("готово роликов %d, суммарно %.1f МБ", len(rendered), total_mb)

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
        subtitle_name: str,
        output,
        has_video: bool,
        framing: Framing,
        plan,
        width: int,
        height: int,
        crf: int,
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
            video_filter = build_filter(plan, framing, width, height, subtitle_name)
            args += ["-filter_complex", video_filter, "-map", "[v]", "-map", "0:a:0"]
        else:
            args += [
                "-f", "lavfi",
                "-i", f"color=c={framing.color}:s={width}x{height}:r=30:d={duration:.3f}",
                "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
                "-filter_complex", f"[0:v]subtitles={subtitle_name}[v]",
                "-map", "[v]", "-map", "1:a:0",
            ]

        args += [
            "-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11",
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
