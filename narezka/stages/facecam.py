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

from narezka.core import detectors, tracking, facecam
from narezka.core.artifacts import Artifact
from narezka.core.clips import SELECTION_NAME, clip_inputs, load_clips
from narezka.core.media import find_source
from narezka.core.stage import Device, Stage, StageContext, StageSkipped

FACECAM_NAME = "facecam.json"

#: Сколько кадров пробовать на клип для поиска вебки. Больше не нужно:
#: вебка неподвижна, и семи проб хватает, чтобы отличить её от лица
#: в проигрываемом видео.
SAMPLES_PER_CLIP = 7

#: Для поиска области контента нужны **близкие** кадры, а не разнесённые.
#: Между пробами в разных концах клипа меняется вообще всё, движение выходит
#: одинаково высоким по всему экрану, и отличить видео от интерфейса нельзя.
#: На первом прогоне из-за этого область не нашлась ни в одном клипе.
MOTION_SAMPLES = 6
MOTION_STEP = 1.0


#: Предел на один кадр. Без него зависший вызов ffmpeg вешает стадию
#: навсегда: subprocess.run без timeout ждёт бесконечно, и снаружи это
#: выглядит не ошибкой, а бесконечной работой.
FRAME_TIMEOUT = 30


def sample_range(source, start: float, end: float, per_second: float, log=None):
    """Кадры подряд с заданной частотой — одним процессом ffmpeg.

    Для слежения кадры нужны сплошной лентой, и запускать ffmpeg на каждый —
    самое дорогое, что можно придумать: на клипе в пятьдесят секунд это 259
    запусков. Замер: так стадия шла 130 секунд на клип, одним процессом —
    секунды. Разбросанные по записи пробы по-прежнему берёт `sample_frames`:
    там перемотка действительно дешевле сплошного декодирования.

    Кадры отдаются сырыми: кодировать их в PNG, чтобы тут же раскодировать
    обратно, — лишняя работа на каждом кадре.
    """
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(source)],
        capture_output=True, text=True, check=False,
    )
    try:
        width, height = (int(v) for v in probe.stdout.strip().split(",")[:2])
    except ValueError:
        if log:
            log.warning("не удалось узнать размер кадра — слежение пропущено")
        return []

    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-i", str(source),
         "-t", f"{end - start:.3f}", "-vf", f"fps={per_second}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        capture_output=True, check=False, timeout=FRAME_TIMEOUT * 10,
    )
    size = width * height * 3
    count = len(result.stdout) // size
    if count == 0:
        if log:
            log.warning("кадры с %.1f по %.1f с не прочитаны", start, end)
        return []
    data = np.frombuffer(result.stdout[: count * size], dtype=np.uint8)
    return list(data.reshape(count, height, width, 3))


def sample_frames(source, times: list[float], log=None):
    """Кадры в заданные моменты. Читаются по одному, а не потоком:
    моменты разбросаны по записи, и перемотка дешевле декодирования."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    frames = []
    for at in times:
        try:
            result = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{at:.2f}", "-i", str(source),
                 "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                capture_output=True, check=False, timeout=FRAME_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            # Один непрочитанный кадр не повод бросать клип: проб несколько,
            # и остальных хватит.
            if log:
                log.warning("кадр на %.1f с не прочитан за %d с", at, FRAME_TIMEOUT)
            continue
        if result.returncode != 0 or not result.stdout:
            continue
        image = cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            frames.append(image)
    return frames


#: Какая доля кадров должна дать лицо, чтобы вести по нему рамку. Меньше
#: половины — траектория состоит в основном из догадок, и слежение выключается:
#: неподвижный кадр честнее рамки, гуляющей по интерполяции.
MIN_TRACK_HITS = 0.5


class FacecamStage(Stage):
    name = "facecam"
    group = "shorts"
    #: v3 — область контента ищется по близким кадрам: на разнесённых
    #: движение одинаково высоко по всему экрану и ничего не различает.
    version = 3
    #: Предел на стадию целиком. Замер: 6.5 с на клип, то есть минута
    #: на десяток. Всё, что дольше на порядок, — зависание, а не работа.
    timeout_seconds = 600
    device = Device.ANY
    optional = True
    description = "Поиск окна вебкамеры для раскладки «сплит»"

    def _track(self, ctx, detector, source, clip) -> dict[str, Any] | None:
        """Сглаженная траектория головы внутри клипа."""
        cfg = ctx.config.output.framing
        positions, moments = _track_points(
            detector, source, clip["start"], clip["end"],
            cfg.track_samples_per_second, ctx.log,
        )
        found = sum(1 for p in positions if p is not None)
        if found < len(positions) * MIN_TRACK_HITS:
            ctx.log.info(
                "клип %d: лицо найдено в %d кадрах из %d — слежение отключено",
                clip["index"], found, len(positions),
            )
            return None

        short = ctx.config.output.short
        # Ширина кропа в пикселях **исходника**, а не готового ролика.
        # Траектория считается в координатах исходника, и мерить мёртвую зону
        # выходной шириной — грубая ошибка: при кропе 404 из кадра 720 высотой
        # зона выходила в 270 px, две трети видимого кадра, и рамка почти
        # не двигалась. Ровно это и было видно на первом собранном ролике.
        source_height = _source_height(source)
        crop_width = max(2.0, source_height * short.width / short.height)
        track = tracking.smooth(
            positions, moments, crop_width,
            smoothing=cfg.track_smoothing,
            dead_zone=cfg.track_dead_zone,
            max_speed=cfg.track_max_speed,
        )
        # Порог прореживания низкий: выражение теперь плоская сумма, глубина
        # вложенности не растёт, и лишние точки стоят только длины строки.
        points = tracking.keyframes(track, threshold=2.0)
        ctx.log.info(
            "клип %d: слежение — находок %d из %d, опорных точек %d, путь %.0f px",
            clip["index"], found, len(positions), len(points), track.travel,
        )
        return {"points": [[round(t, 3), round(x, 1)] for t, x in points], "travel": round(track.travel, 1)}

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        return clip_inputs(ctx.paths)

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.analysis / FACECAM_NAME)]

    @staticmethod
    def _wants_track(ctx: StageContext) -> bool:
        """Нужна ли траектория головы при нынешних настройках.

        Считать её всегда — это лишний проход детектора по каждому клипу,
        самая дорогая часть стадии. Считать никогда — раскладки со слежением
        молча не работают.
        """
        cfg = ctx.config.output.framing
        return cfg.layout == "track" or (cfg.follow_face and cfg.layout in ("split", "camera"))

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        cfg = ctx.config.output.framing
        slice_ = {"backend": ctx.config.detector.backend, "samples": SAMPLES_PER_CLIP}
        # Просьба о слежении входит в ключ: без неё выбор раскладки со
        # слежением на уже разобранной записи ничего не менял — стадия
        # бралась из кэша, траектории в ней не было, и рендер молча
        # собирал обычный кадр.
        slice_["track"] = (
            {
                "samples_per_second": cfg.track_samples_per_second,
                "smoothing": cfg.track_smoothing,
                "dead_zone": cfg.track_dead_zone,
                "max_speed": cfg.track_max_speed,
            }
            if self._wants_track(ctx)
            else None
        )
        return slice_

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

        for done, clip in enumerate(clips, start=1):
            ctx.progress(done, len(clips), "поиск вебки")
            step = (clip["end"] - clip["start"]) / (SAMPLES_PER_CLIP + 1)
            times = [clip["start"] + step * (i + 1) for i in range(SAMPLES_PER_CLIP)]
            frames = sample_frames(source, times, log=ctx.log)
            if not frames:
                continue

            result = facecam.detect(detector, frames)
            if result is None:
                ctx.log.info("клип %d: вебка не найдена", clip["index"])
                continue

            entry = result.as_dict()
            # Траектория головы для раскладки «слежение». Считается здесь же:
            # кадры уже прочитаны, а читать их второй раз — самая дорогая
            # часть стадии.
            if self._wants_track(ctx):
                entry["track"] = self._track(ctx, detector, source, clip)
            if not result.full_frame:
                # Область контента нужна нижней полосе сплита: без неё она
                # режется по центру и захватывает интерфейс плеера.
                middle = (clip["start"] + clip["end"]) / 2
                burst = sample_frames(
                    source,
                    [middle + i * MOTION_STEP for i in range(MOTION_SAMPLES)],
                    log=ctx.log,
                )
                content = facecam.detect_content(burst, exclude=result.rect)
                if content is not None:
                    entry["content"] = content.as_dict()
            found[str(clip["index"])] = entry
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


def _track_points(
    detector, source, start: float, end: float, per_second: float, log
) -> tuple[list[float | None], list[float]]:
    """Положения лица по клипу: центр по горизонтали и моменты времени.

    Пять-десять раз в секунду достаточно (§17): между находками положение
    получается интерполяцией, а лицо не движется быстрее.
    """
    frames = sample_range(source, start, end, per_second, log=log)
    step = 1.0 / per_second
    moments = [start + i * step for i in range(len(frames))]

    positions: list[float | None] = []
    for frame in frames:
        faces = detector.detect(frame)
        best = max(faces, key=lambda f: f[4]) if faces else None
        positions.append(best[0] + best[2] / 2 if best else None)
    return positions, [m - start for m in moments[: len(frames)]]


def _source_height(source) -> float:
    """Высота кадра исходника. Нужна, чтобы считать кроп в его координатах."""
    import cv2  # noqa: PLC0415

    capture = cv2.VideoCapture(str(source))
    try:
        return float(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720.0
    finally:
        capture.release()
