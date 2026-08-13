"""Стадия render: готовые вертикальные ролики.

BAZA.md §37 и §59. Нарезка идёт с перекодированием, а не копированием потока:
копирование режет по ключевым кадрам с ошибкой в несколько секунд, что для
клипа неприемлемо.

Кадрирование на этом этапе — блюр-подложка ([§61](BAZA.md#61)): исходный кадр
целиком вписывается по ширине, фон заполняется его же размытой увеличенной
копией. Это не нарушает запрет §39 на статичный центральный кроп, потому что
ничего не отрезается. Слежение за объектом появится на этапе 4.
"""

from __future__ import annotations

from typing import Any

from narezka.core.artifacts import Artifact
from narezka.core.media import find_source, run_tool
from narezka.core.stage import Device, Stage, StageContext, StageSkipped
from narezka.stages.candidates import CANDIDATES_NAME
from narezka.stages.subtitles import INDEX_NAME

SHORTS_INDEX = "index.json"

#: Фон для источников без картинки. Тёмный, чтобы белые субтитры читались.
AUDIO_BACKGROUND = "0x14171c"


class RenderStage(Stage):
    name = "render"
    version = 1
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
        return {
            **ctx.config.output.model_dump(),
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
        metadata = Artifact(ctx.paths.metadata)
        has_video = bool(metadata.read_json().get("has_video")) if metadata.exists() else True

        if not has_video:
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
                "background": "blur" if has_video else "solid",
                "files": rendered,
            }
        )
        total_mb = sum(f["size_bytes"] for f in rendered) / 1024**2
        ctx.log.info("готово роликов %d, суммарно %.1f МБ", len(rendered), total_mb)

    def _command(
        self,
        *,
        source,
        start: float,
        duration: float,
        subtitle_name: str,
        output,
        has_video: bool,
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
            # Подложка — размытая увеличенная копия кадра, поверх неё исходник
            # целиком по ширине. Ничего не отрезается (§39).
            video_filter = (
                f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},gblur=sigma=28[bg];"
                f"[0:v]scale={width}:-2[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
                f"[base]subtitles={subtitle_name}[v]"
            )
            args += ["-filter_complex", video_filter, "-map", "[v]", "-map", "0:a:0"]
        else:
            args += [
                "-f", "lavfi",
                "-i", f"color=c={AUDIO_BACKGROUND}:s={width}x{height}:r=30:d={duration:.3f}",
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
