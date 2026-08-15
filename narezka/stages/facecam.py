"""Стадия facecam: поиск вебкамеры для раскладки «сплит».

BAZA.md §17, §61. Ищет окно вебки **по клипу**, а не один раз на запись.
Спецификация предполагала, что вебка неподвижна всю запись, и в пределах
одной сцены это так. Но замер показал, что стрим переключается между
полноэкранной камерой и демонстрацией экрана, и раскладка меняется вместе
с ним — значит, судить надо по тому отрезку, который пойдёт в ролик.

Стадия опциональна (§43): нет детектора, нет лица или запись без картинки —
кадрируем как обычно.
"""

from __future__ import annotations

import subprocess
from typing import Any

from narezka.core import detectors, facecam
from narezka.core.artifacts import Artifact
from narezka.core.clips import SELECTION_NAME, load_clips
from narezka.core.media import find_source
from narezka.core.stage import Device, Stage, StageContext, StageSkipped

FACECAM_NAME = "facecam.json"

#: Сколько кадров пробовать на клип. Больше не нужно: вебка неподвижна,
#: и семи проб хватает, чтобы отличить её от лица в проигрываемом видео.
SAMPLES_PER_CLIP = 7


def sample_frames(source, times: list[float]):
    """Кадры в заданные моменты. Читаются по одному, а не потоком:
    моменты разбросаны по записи, и перемотка дешевле декодирования."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    frames = []
    for at in times:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{at:.2f}", "-i", str(source),
             "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True, check=False,
        )
        if result.returncode != 0 or not result.stdout:
            continue
        image = cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            frames.append(image)
    return frames


class FacecamStage(Stage):
    name = "facecam"
    version = 1
    device = Device.ANY
    optional = True
    description = "Поиск окна вебкамеры для раскладки «сплит»"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        selection = Artifact(ctx.paths.analysis / SELECTION_NAME)
        return [selection if selection.exists() else Artifact(ctx.paths.analysis / "candidates.json")]

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.analysis / FACECAM_NAME)]

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        return {"backend": ctx.config.detector.backend, "samples": SAMPLES_PER_CLIP}

    def check_available(self, ctx: StageContext) -> str | None:
        metadata = Artifact(ctx.paths.metadata)
        if metadata.exists() and not metadata.read_json().get("has_video", True):
            return "в источнике нет картинки"
        return detectors.available(ctx.config.detector.backend)

    def run(self, ctx: StageContext) -> None:
        clips, _ = load_clips(ctx.paths)
        if not clips:
            raise StageSkipped("нет клипов")

        try:
            detector = detectors.create(ctx.config.detector.backend)
        except detectors.DetectorError as exc:
            raise StageSkipped(str(exc)) from exc

        source = find_source(ctx.paths.source)
        found: dict[str, Any] = {}

        for clip in clips:
            step = (clip["end"] - clip["start"]) / (SAMPLES_PER_CLIP + 1)
            times = [clip["start"] + step * (i + 1) for i in range(SAMPLES_PER_CLIP)]
            frames = sample_frames(source, times)
            if not frames:
                continue

            result = facecam.detect(detector, frames)
            if result is None:
                ctx.log.info("клип %d: вебка не найдена", clip["index"])
                continue

            found[str(clip["index"])] = result.as_dict()
            ctx.log.info(
                "клип %d: %s %dx%d, уверенность %.2f",
                clip["index"],
                "камера во весь кадр" if result.full_frame else "вебка",
                result.rect.width, result.rect.height, result.confidence,
            )

        if not found:
            raise StageSkipped("вебка не найдена ни в одном клипе")

        overlays = sum(1 for v in found.values() if not v["full_frame"])
        Artifact(ctx.paths.analysis / FACECAM_NAME).write_json(
            {
                "backend": ctx.config.detector.backend,
                "clips": found,
                "stats": {
                    "clips": len(clips),
                    "found": len(found),
                    "with_overlay": overlays,
                },
            }
        )
        ctx.log.info(
            "вебка найдена в %d клипах из %d, наложением в %d — для них доступен сплит",
            len(found), len(clips), overlays,
        )
