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
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "models"

#: Короткофокусная модель. Ищет лицо, занимающее заметную долю кадра, —
#: ровно наш случай, потому что детектор запускается по вырезанной области
#: вебки, а не по полному кадру (см. narezka/core/facecam.py).
DEFAULT_MODEL = "blaze_face_short_range.tflite"

#: YOLOX-tiny в ONNX: 20 МБ, считается на CPU через onnxruntime, без torch.
#: В репозиторий не кладётся — в отличие от 230-килобайтной модели лиц, это
#: заметный вес в каждом клоне ради бэкенда, который ещё не доказал, что он
#: лучше. Отсутствие файла честно сообщается через available().
YOLOX_MODEL = "yolox_tiny.onnx"

#: Вход YOLOX-tiny и шаги сетки, по которым раскодируется выход.
YOLOX_SIZE = 416
YOLOX_STRIDES = (8, 16, 32)

#: Класс «человек» в COCO. YOLOX ищет людей, а не лица: для поиска окна вебки
#: этого достаточно — человек сидит в кадре камеры постоянно, — но границы
#: получаются по фигуре, а не по лицу, и это разные вещи.
PERSON_CLASS = 0

@dataclass(frozen=True)
class Backend:
    """Бэкенд детектора и его состояние.

    Нереализованные перечислены намеренно: §43 требует сменяемости, а §52 —
    сравнения трёх на своём материале. Показывать их в интерфейсе честнее,
    чем делать вид, что выбора нет, — но только с прямой пометкой, что они
    не работают. Заглушка, притворяющаяся рабочей, хуже её отсутствия.
    """

    name: str
    label: str
    license: str
    #: Реализован ли. Нет — выбрать нельзя, и в интерфейсе это видно.
    implemented: bool
    note: str


BACKENDS: tuple[Backend, ...] = (
    Backend(
        "mediapipe", "MediaPipe", "Apache-2.0", True,
        "работает: лица на CPU, модель 230 КБ в поставке",
    ),
    Backend(
        "yolox", "YOLOX", "Apache-2.0", True,
        "работает: люди и объекты через ONNX, без torch",
    ),
    Backend(
        "ultralytics", "Ultralytics YOLO", "AGPL-3.0", False,
        "не реализован: обычно точнее, но AGPL ограничит публичный запуск",
    ),
)


def describe_backends() -> list[dict[str, object]]:
    """Список бэкендов для интерфейса — с причиной недоступности."""
    result = []
    for backend in BACKENDS:
        reason = available(backend.name) if backend.implemented else backend.note
        result.append(
            {
                "name": backend.name,
                "label": backend.label,
                "license": backend.license,
                "implemented": backend.implemented,
                "available": backend.implemented and reason is None,
                "note": reason or backend.note,
            }
        )
    return result


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


class YoloxPeople:
    """YOLOX-tiny через onnxruntime. Отдаёт людей, а не лица.

    Тот же интерфейс, что и у детектора лиц (§43), поэтому подставляется
    в поиск вебки без правки самого поиска. Но искомое другое: в вырезанном
    окне камеры человек занимает почти весь кадр, тогда как лицо — часть,
    и устойчивость положения считается по фигуре.
    """

    def __init__(self, model: str = YOLOX_MODEL, min_confidence: float = 0.3) -> None:
        path = MODELS_DIR / model
        if not path.is_file():
            raise DetectorError(f"нет файла модели {path}")
        try:
            import onnxruntime  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover — зависит от установки
            raise DetectorError(f"onnxruntime не установлен: {exc}") from exc

        self._min_confidence = min_confidence
        self._session = onnxruntime.InferenceSession(
            str(path), providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name

    def detect(self, image: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        import cv2  # noqa: PLC0415

        if image.size == 0:
            return []
        height, width = image.shape[:2]
        # Пропорции сохраняются, поле дополняется — иначе координаты
        # разъедутся при обратном пересчёте.
        scale = min(YOLOX_SIZE / width, YOLOX_SIZE / height)
        resized = cv2.resize(image, (int(width * scale), int(height * scale)))
        padded = np.full((YOLOX_SIZE, YOLOX_SIZE, 3), 114, dtype=np.uint8)
        padded[: resized.shape[0], : resized.shape[1]] = resized

        blob = padded.transpose(2, 0, 1)[None].astype(np.float32)
        raw = self._session.run(None, {self._input: blob})[0][0]

        boxes = [b for b in self._decode(raw) if b[4] >= self._min_confidence]
        return [
            (int(x / scale), int(y / scale), int(w / scale), int(h / scale), float(score))
            for x, y, w, h, score in _suppress_overlaps(boxes)
        ]

    @staticmethod
    def _decode(raw: np.ndarray) -> list[tuple[float, float, float, float, float]]:
        """Раскодирует сетку YOLOX в прямоугольники левого верхнего угла."""
        grids, expanded = [], []
        for stride in YOLOX_STRIDES:
            side = YOLOX_SIZE // stride
            xv, yv = np.meshgrid(np.arange(side), np.arange(side))
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            expanded.append(np.full((side * side, 1), stride))
        grid = np.concatenate(grids, 0)
        strides = np.concatenate(expanded, 0)

        centres = (raw[:, :2] + grid) * strides
        sizes = np.exp(raw[:, 2:4]) * strides
        scores = raw[:, 4] * raw[:, 5 + PERSON_CLASS]

        result = []
        for (cx, cy), (w, h), score in zip(centres, sizes, scores, strict=True):
            result.append((cx - w / 2, cy - h / 2, w, h, score))
        return result


#: Насколько сильно должны перекрываться прямоугольники, чтобы считаться одним.
NMS_OVERLAP = 0.45


def _suppress_overlaps(boxes: list) -> list:
    """Оставляет по одному прямоугольнику на объект.

    Сетка YOLOX выдаёт десятки перекрывающихся находок на одного человека:
    замер на кадре стрима — 25 штук. Без подавления вызывающий код видит
    толпу там, где сидит один стример.
    """
    ordered = sorted(boxes, key=lambda b: b[4], reverse=True)
    kept: list = []
    for box in ordered:
        if all(_overlap(box, other) < NMS_OVERLAP for other in kept):
            kept.append(box)
    return kept


def _overlap(a, b) -> float:
    """Доля пересечения к объединению (IoU)."""
    ax, ay, aw, ah = a[:4]
    bx, by, bw, bh = b[:4]
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


def available(backend: str = "mediapipe") -> str | None:
    """Причина, по которой бэкенд недоступен. None — доступен."""
    known = {b.name: b for b in BACKENDS}
    if backend not in known:
        return f"неизвестный бэкенд {backend}"
    if not known[backend].implemented:
        return known[backend].note
    modules = {"mediapipe": ("mediapipe", "cv2"), "yolox": ("onnxruntime", "cv2")}[backend]
    for module in modules:
        if importlib.util.find_spec(module) is None:
            return f"модуль {module} не установлен"
    model = DEFAULT_MODEL if backend == "mediapipe" else YOLOX_MODEL
    if not (MODELS_DIR / model).is_file():
        return f"нет файла модели {model} — положите его в {MODELS_DIR}"
    return None


def create(backend: str = "mediapipe", **kwargs):
    reason = available(backend)
    if reason:
        raise DetectorError(reason)
    return {"mediapipe": MediaPipeFaces, "yolox": YoloxPeople}[backend](**kwargs)
