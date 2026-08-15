"""Поиск прямоугольника вебкамеры на записи стрима.

BAZA.md §17, §61. Нужен для раскладки «сплит»: лицо стримера сверху,
приближённый контент снизу. Это самый ценный вид нарезки для разбора видео —
зритель одновременно видит и то, что смотрят, и реакцию.

**Почему не просто детекция лица по кадру.** Замерено на записи: на полном
кадре 1280×720 детектор находит лица в самом видео, которое смотрит стример
(уверенность 0.5), а лицо в вебке размером 90 px не находит вовсе — оно
теряется при масштабировании кадра под вход модели.

Тот же детектор на **вырезанной области вебки** даёт уверенность 0.95: там
лицо занимает заметную долю кадра. Отсюда алгоритм: перебрать правдоподобные
прямоугольники, вырезать каждый и спросить детектор. Вебка — тот, где лицо
находится уверенно и **на одном месте** во всех пробах.

Устойчивость положения здесь такой же признак, как уверенность: вебка почти
весь стрим стоит неподвижно, а лицо в проигрываемом видео скачет.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

#: Доли ширины кадра, при которых имеет смысл искать вебку. Меньше 12% —
#: лицо будет нечитаемым в вертикальном ролике, больше 45% — это уже
#: полноэкранная камера, а не наложение.
CANDIDATE_WIDTHS = (0.15, 0.22, 0.30, 0.40)

#: Пропорция окна вебки. Камеры дают 16:9, реже 4:3.
CANDIDATE_RATIOS = (16 / 9, 4 / 3)

#: Ниже этой уверенности находка не считается лицом.
MIN_CONFIDENCE = 0.55

#: Насколько может гулять центр лица между пробами, в долях ширины окна.
#: Вебка стоит неподвижно; лицо в проигрываемом видео скачет по кадру.
MAX_DRIFT = 0.25

#: В скольких пробах из всех лицо должно найтись, чтобы окну верить.
#: Замер: за сорок секунд стрима сцена успевает смениться, и в части проб
#: лица нет вовсе. Половины достаточно — а цена ошибки здесь мала: не нашли
#: вебку, значит просто не делаем сплит и кадрируем как обычно.
MIN_HIT_RATIO = 0.45


class FaceDetector(Protocol):
    """Интерфейс детектора (§43): бэкенд заменяется без правки этого модуля."""

    def detect(self, image: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        """Прямоугольники лиц (x, y, ширина, высота, уверенность)."""
        ...


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def clamp(self, frame_width: int, frame_height: int) -> "Rect":
        x = max(0, min(self.x, frame_width - 1))
        y = max(0, min(self.y, frame_height - 1))
        return Rect(x, y, min(self.width, frame_width - x), min(self.height, frame_height - y))

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class FaceCam:
    rect: Rect
    #: Само лицо в координатах кадра. Верхнюю полосу сплита строим по нему,
    #: а не по найденному окну: окно ищется грубо, по углам, и захватывает
    #: панель браузера или край экрана. Лицо задаёт кадр точно.
    face: Rect
    confidence: float
    #: Доля проб, в которых лицо нашлось на том же месте.
    stability: float
    #: Занимает ли камера весь кадр — тогда сплит не нужен, достаточно кропа.
    full_frame: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.rect.as_dict(),
            "face": self.face.as_dict(),
            "confidence": round(self.confidence, 3),
            "stability": round(self.stability, 3),
            "full_frame": self.full_frame,
        }


def candidate_rects(frame_width: int, frame_height: int) -> list[Rect]:
    """Правдоподобные окна вебки: углы кадра при нескольких размерах.

    Углы, а не вся сетка: наложение камеры ставят к краю, чтобы оно не
    закрывало содержимое. Полный кадр проверяется отдельно — это случай
    стрима без демонстрации экрана.
    """
    rects: list[Rect] = []
    for width_share in CANDIDATE_WIDTHS:
        width = int(frame_width * width_share)
        for ratio in CANDIDATE_RATIOS:
            height = int(width / ratio)
            if height >= frame_height:
                continue
            for x in (0, frame_width - width):
                for y in (0, frame_height - height):
                    rects.append(Rect(x, y, width, height).clamp(frame_width, frame_height))
    # Порядок не важен, а вот повторы — лишние вызовы детектора.
    unique = {(r.x, r.y, r.width, r.height): r for r in rects}
    return list(unique.values())


def _probe(detector: FaceDetector, frames: list[np.ndarray], rect: Rect) -> tuple[float, float, Rect | None]:
    """Средняя уверенность, устойчивость положения и усреднённое лицо."""
    confidences: list[float] = []
    centres: list[tuple[float, float]] = []
    boxes: list[tuple[int, int, int, int]] = []

    for frame in frames:
        crop = frame[rect.y : rect.bottom, rect.x : rect.right]
        if crop.size == 0:
            continue
        faces = detector.detect(crop)
        if not faces:
            continue
        x, y, w, h, score = max(faces, key=lambda f: f[4])
        if score < MIN_CONFIDENCE:
            continue
        confidences.append(score)
        centres.append(((x + w / 2) / rect.width, (y + h / 2) / rect.height))
        # Лицо переводится в координаты полного кадра сразу: дальше окно
        # уже не нужно, а лицо нужно.
        boxes.append((rect.x + x, rect.y + y, w, h))

    if not confidences:
        return 0.0, 0.0, None

    hit_ratio = len(confidences) / len(frames)
    array = np.array(centres)
    drift = float(np.hypot(*array.std(axis=0))) if len(centres) > 1 else 0.0
    # Разброс центра переводится в устойчивость: неподвижная камера даёт
    # почти нулевой разброс, лицо в проигрываемом видео — большой.
    stability = hit_ratio * max(0.0, 1.0 - drift / MAX_DRIFT)
    mean_box = np.array(boxes).mean(axis=0).astype(int)
    face = Rect(int(mean_box[0]), int(mean_box[1]), int(mean_box[2]), int(mean_box[3]))
    return float(np.mean(confidences)), stability, face


def detect(
    detector: FaceDetector,
    frames: list[np.ndarray],
    *,
    min_stability: float = MIN_HIT_RATIO,
) -> FaceCam | None:
    """Ищет окно вебки. None — не нашлось, раскладка «сплит» невозможна."""
    if not frames:
        return None

    height, width = frames[0].shape[:2]

    # Полный кадр проверяется первым: если стрим и есть камера во весь экран,
    # резать его на части незачем.
    full = Rect(0, 0, width, height)
    full_conf, full_stability, full_face = _probe(detector, frames, full)

    best: tuple[float, Rect, float, float, Rect] | None = None
    for rect in candidate_rects(width, height):
        confidence, stability, face = _probe(detector, frames, rect)
        if stability < min_stability or face is None:
            continue
        # Оценка окна: уверенность, взвешенная устойчивостью положения.
        score = confidence * stability
        if best is None or score > best[0]:
            best = (score, rect, confidence, stability, face)

    full_score = full_conf * full_stability
    # Наложение выигрывает у полного кадра только с запасом: на полноэкранной
    # камере угловое окно тоже поймает лицо, и без запаса победил бы случайный
    # угол, разрезав целый кадр без нужды.
    if best is not None and best[0] > full_score * 1.2:
        return FaceCam(
            rect=best[1], face=best[4], confidence=best[2],
            stability=best[3], full_frame=False,
        )

    if full_stability >= min_stability and full_face is not None:
        return FaceCam(
            rect=full, face=full_face, confidence=full_conf,
            stability=full_stability, full_frame=True,
        )

    return None


# --- область проигрываемого контента ---------------------------------------

#: Доля кадра, ниже которой область не считается контентом: мелкие
#: подвижные пятна — это курсор, бегущая строка чата или значки.
MIN_CONTENT_SHARE = 0.05

#: Насколько выше медианы должно быть движение, чтобы точка считалась
#: подвижной. Порог относительный: у тёмной записи и у яркой абсолютный
#: разброс отличается в разы.
MOTION_FACTOR = 1.8


def detect_content(frames: list[np.ndarray], exclude: Rect | None = None) -> Rect | None:
    """Прямоугольник проигрываемого контента по движению между кадрами.

    Нужен нижней полосе сплита. Без него она режется по центру области под
    вебкой и захватывает интерфейс: панель плеера, ленту сообщений, поля
    страницы. Смысла в них нет, а место в кадре они занимают.

    Признак — движение: у проигрываемого видео оно непрерывное, а интерфейс
    браузера неподвижен. Область вебки исключается: она тоже подвижна,
    и без исключения победила бы она сама.
    """
    import cv2  # noqa: PLC0415

    if len(frames) < 2:
        return None

    height, width = frames[0].shape[:2]
    # Считаем на уменьшенных кадрах: границы области нужны с точностью
    # до десятка пикселей, а работы становится в двадцать раз меньше.
    scale = 4
    small = [
        cv2.cvtColor(cv2.resize(f, (width // scale, height // scale)), cv2.COLOR_BGR2GRAY).astype(np.float32)
        for f in frames
    ]
    motion = np.stack(small).std(axis=0)

    if exclude is not None:
        motion[
            exclude.y // scale : max(exclude.bottom // scale, exclude.y // scale + 1),
            exclude.x // scale : max(exclude.right // scale, exclude.x // scale + 1),
        ] = 0.0

    threshold = max(float(np.median(motion)) * MOTION_FACTOR, 4.0)
    mask = (motion > threshold).astype(np.uint8)
    if mask.sum() == 0:
        return None

    # Смыкание закрывает разрывы: ровные участки внутри кадра видео
    # (небо, стена) движения не дают и рвут область на куски.
    mask = cv2.morphologyEx(mask * 255, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count < 2:
        return None

    index = max(range(1, count), key=lambda i: stats[i, cv2.CC_STAT_AREA])
    x, y, w, h, area = stats[index]
    if area / mask.size < MIN_CONTENT_SHARE:
        return None

    # Рамка компоненты обычно шире самого видео: страница вокруг тоже слегка
    # шевелится и смыкание притягивает её к области. Подрезаем края по строкам
    # и столбцам, где движение заметно слабее, чем в середине области.
    box = motion[y : y + h, x : x + w]
    top, bottom = _dense_span(box.mean(axis=1))
    left, right = _dense_span(box.mean(axis=0))

    return Rect(
        (x + left) * scale,
        (y + top) * scale,
        max(right - left, 1) * scale,
        max(bottom - top, 1) * scale,
    ).clamp(width, height)


#: Доля от самой подвижной строки, ниже которой край считается не контентом,
#: а окружением: страницей, панелью плеера, полями.
EDGE_KEEP = 0.45


def _dense_span(profile: np.ndarray) -> tuple[int, int]:
    """Границы участка, где движение держится выше доли от максимума."""
    if profile.size == 0:
        return 0, 1
    keep = np.flatnonzero(profile >= profile.max() * EDGE_KEEP)
    if keep.size == 0:
        return 0, int(profile.size)
    return int(keep[0]), int(keep[-1] + 1)
