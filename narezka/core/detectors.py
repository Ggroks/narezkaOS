"""Детекторы лиц за общим интерфейсом.

BAZA.md §43 и §49. Бэкенд меняется через конфиг, а не правкой стадий.
Сравнение трёх реализаций на своём материале — отдельная задача этапа 4;
пока подключён MediaPipe: Apache-2.0, работает на CPU, модель весит 230 КБ.

Модель лежит в репозитории рядом со шрифтами (§60): скачивать её при первом
запуске значит поставить работу в зависимость от сети и от того, что файл
по ссылке не изменится.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "models"

#: Короткофокусная модель. Ищет лицо, занимающее заметную долю кадра, —
#: ровно наш случай, потому что детектор запускается по вырезанной области
#: вебки, а не по полному кадру (см. narezka/core/facecam.py).
DEFAULT_MODEL = "blaze_face_short_range.tflite"

BACKENDS = ("mediapipe",)


class DetectorError(RuntimeError):
    """Детектор недоступен."""


class MediaPipeFaces:
    """Обёртка над MediaPipe Face Detector.

    Создаётся один раз и переиспользуется: инициализация тяжелее самого
    вызова, а кадров на клип десятки.
    """

    def __init__(self, model: str = DEFAULT_MODEL, min_confidence: float = 0.3) -> None:
        path = MODELS_DIR / model
        if not path.is_file():
            raise DetectorError(f"нет файла модели {path}")

        try:
            from mediapipe.tasks.python import BaseOptions, vision  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover — зависит от установки
            raise DetectorError(f"mediapipe не установлен: {exc}") from exc

        self._vision = vision
        self._detector = vision.FaceDetector.create_from_options(
            vision.FaceDetectorOptions(
                base_options=BaseOptions(model_asset_path=str(path)),
                min_detection_confidence=min_confidence,
            )
        )

    def detect(self, image: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        """Лица на изображении BGR. Возвращает (x, y, ширина, высота, уверенность)."""
        import cv2  # noqa: PLC0415
        import mediapipe as mp  # noqa: PLC0415

        if image.size == 0:
            return []
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        result = self._detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))

        faces = []
        for detection in result.detections:
            box = detection.bounding_box
            score = detection.categories[0].score if detection.categories else 0.0
            faces.append((box.origin_x, box.origin_y, box.width, box.height, float(score)))
        return faces


def available(backend: str = "mediapipe") -> str | None:
    """Причина, по которой бэкенд недоступен. None — доступен."""
    if backend != "mediapipe":
        return f"бэкенд {backend} ещё не реализован"
    for module in ("mediapipe", "cv2"):
        if importlib.util.find_spec(module) is None:
            return f"модуль {module} не установлен"
    if not (MODELS_DIR / DEFAULT_MODEL).is_file():
        return f"нет файла модели {DEFAULT_MODEL}"
    return None


def create(backend: str = "mediapipe", **kwargs) -> MediaPipeFaces:
    reason = available(backend)
    if reason:
        raise DetectorError(reason)
    return MediaPipeFaces(**kwargs)
