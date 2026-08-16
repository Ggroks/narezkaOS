"""Смена сцен: где картинка меняется целиком, а не постепенно.

BAZA.md §9, §43. Нужна для двух вещей: подрезать границы клипа к смене плана,
чтобы ролик не начинался с середины кадра, и резать вместо панорамирования
при слежении за объектом — панорама через склейку укачивает.

**Почему порог адаптивный, а не постоянный.** Постоянный порог настраивается
под конкретный материал и на другом разваливается: на шутере резкий поворот
мыши меняет кадр сильнее, чем настоящая склейка в разговорном стриме. Поэтому
кадровая разница сравнивается не с числом, а с собственным недавним фоном:
всплеск считается сменой, если он резко выделяется на фоне последних секунд.

**Медиана и MAD, а не среднее и дисперсия.** Сами склейки — это выбросы, и
среднее с дисперсией они же и раздувают, из-за чего порог уезжает вверх и
следующие склейки перестают находиться.

**Минимальная длина сцены обязательна.** Без неё быстрая последовательность
кадров даёт десятки «сцен» подряд, а одна склейка на границе двух кадров —
две штуки вместо одной.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Во сколько раз разница должна превышать типичную, чтобы считаться сменой.
#: Замерять надо на своём материале: на разговорном стриме хватает меньшего,
#: на геймплее приходится поднимать.
DEFAULT_SENSITIVITY = 6.0

#: Короче этого сцены не бывает. Без ограничения одна склейка на границе двух
#: кадров даёт две смены вместо одной, а быстрая нарезка — десятки подряд.
MIN_SCENE_SECONDS = 1.5

#: Пол типичной разницы. Идеально статичная картинка даёт медиану ровно ноль,
#: и отношение к ней не определено — а вернуть при этом «различить нельзя»
#: значит соврать ровно наоборот: там-то склейка как раз видна отчётливее
#: всего. Тот же пол применяется к разбросу при поиске порога.
DIFFERENCE_FLOOR = 0.002

#: Длина скользящего окна, по которому берётся типичный уровень разницы.
#: Секунд двадцать: короче — окно само захватывается всплеском, длиннее —
#: не поспевает за сменой характера материала.
BACKGROUND_SECONDS = 20.0


#: Во сколько раз сильнейшая разница должна превосходить типичную, чтобы
#: считать, что склейки в материале вообще есть. Замер на своих записях:
#: мультфильм даёт 18x — планы разделены отчётливо; стрим 5x — сплошная
#: съёмка без монтажа, и «сцены» там будут всплесками движения, а не
#: склейками. Ниже порога сигнал честнее объявить неразличающим, чем
#: выдать список моментов, которым нельзя верить.
MIN_SEPARATION = 10.0


@dataclass(frozen=True)
class SceneReport:
    #: Моменты смены сцен, секунды от начала записи.
    cuts: list[float]
    #: Кадровая разница — по ней видно, различает сигнал что-нибудь или нет.
    difference: np.ndarray
    step: float

    @property
    def count(self) -> int:
        return len(self.cuts)

    @property
    def separation(self) -> float:
        """Во сколько раз сильнейшая разница превосходит типичную.

        Это мера того, есть ли в материале склейки вообще. При сплошной
        съёмке без монтажа она низкая, и найденные «сцены» — просто всплески
        движения.
        """
        if self.difference.size == 0:
            return 0.0
        typical = max(float(np.median(self.difference)), DIFFERENCE_FLOOR)
        return float(self.difference.max() / typical)

    @property
    def reliable(self) -> bool:
        """Стоит ли доверять найденным сценам.

        Отдельное свойство, а не пустой список: «склеек не нашлось» и
        «материал не позволяет их различить» — разные ответы, и второй
        должен быть виден, а не притворяться первым (§54).
        """
        return self.separation >= MIN_SEPARATION and bool(self.cuts)

    def scenes(self, duration: float) -> list[tuple[float, float]]:
        """Границы сцен как отрезки."""
        bounds = [0.0, *self.cuts, duration]
        return [(a, b) for a, b in zip(bounds, bounds[1:], strict=False) if b > a]

    def nearest(self, at: float, window: float = 3.0) -> float | None:
        """Ближайшая смена сцены к моменту, если она в пределах окна.

        Нужна, чтобы подтянуть границу клипа к склейке: ролик, начинающийся
        с середины плана, выглядит обрезанным.
        """
        if not self.cuts:
            return None
        nearest = min(self.cuts, key=lambda c: abs(c - at))
        return nearest if abs(nearest - at) <= window else None


def frame_difference(frames: np.ndarray) -> np.ndarray:
    """Средняя абсолютная разница между соседними кадрами, 0–1.

    Кадры ожидаются уже уменьшенными и в оттенках серого: цвет и разрешение
    здесь только замедляют, а разницу планов видно и на миниатюре.
    """
    if frames.shape[0] < 2:
        return np.zeros(0, dtype=np.float64)
    flat = frames.reshape(frames.shape[0], -1).astype(np.float64)
    return np.abs(np.diff(flat, axis=0)).mean(axis=1) / 255.0


def find_cuts(
    difference: np.ndarray,
    step: float,
    *,
    sensitivity: float = DEFAULT_SENSITIVITY,
    min_scene: float = MIN_SCENE_SECONDS,
    background_seconds: float = BACKGROUND_SECONDS,
) -> list[float]:
    """Моменты смены сцен по кадровой разнице."""
    if difference.size == 0 or step <= 0:
        return []

    window = max(3, int(background_seconds / step))
    cuts: list[float] = []
    last = -min_scene

    for index in range(difference.size):
        # Фон берётся до текущего кадра, а не вокруг него: иначе сам всплеск
        # входит в свой же фон и занижает собственную выразительность.
        begin = max(0, index - window)
        history = difference[begin:index]
        if history.size < 3:
            continue

        typical = float(np.median(history))
        spread = float(np.median(np.abs(history - typical)))
        # Полностью статичная картинка даёт нулевой разброс, и любое дрожание
        # выглядело бы сменой. Пол разброса не даёт делить на почти ноль.
        threshold = typical + sensitivity * max(spread, DIFFERENCE_FLOOR)

        at = index * step
        if difference[index] > threshold and at - last >= min_scene:
            cuts.append(round(at, 3))
            last = at

    return cuts


def detect(
    frames: np.ndarray,
    step: float,
    *,
    sensitivity: float = DEFAULT_SENSITIVITY,
    min_scene: float = MIN_SCENE_SECONDS,
) -> SceneReport:
    """Смены сцен по последовательности кадров, снятых с шагом `step`."""
    difference = frame_difference(frames)
    cuts = find_cuts(difference, step, sensitivity=sensitivity, min_scene=min_scene)
    return SceneReport(cuts=cuts, difference=difference, step=step)
