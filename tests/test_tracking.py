"""Сглаживание траектории кадрирования.

Проверяется главным образом то, чего быть не должно: дрожания, рывков и
проездов в пустоту. Дёргающийся кадр — не косметика, от него укачивает.
"""

import numpy as np
import pytest

from narezka.core.tracking import Track, crop_centre, smooth

SOURCE = 1280.0
#: Ширина видимого кадра: от неё считаются мёртвая зона и предел скорости.
WIDTH = 600.0


def times(n: int, step: float = 0.1) -> list[float]:
    return [i * step for i in range(n)]


def test_noise_does_not_move_the_frame():
    """Мелкое дрожание детекции не двигает рамку.

    Без мёртвой зоны рамка отзывалась бы на каждый пиксель шума.
    """
    rng = np.random.default_rng(5)
    noisy = [640.0 + rng.normal(0, 8) for _ in range(60)]

    track = smooth(noisy, times(60), WIDTH)

    assert track.travel < 5.0, f"рамка дрожит: путь {track.travel:.1f} px"


def test_real_movement_is_followed():
    """Заметное движение рамка отслеживает."""
    moving = [400.0 + i * 10 for i in range(60)]
    track = smooth(moving, times(60), WIDTH)

    assert track.x[-1] > track.x[0] + 300, "рамка не догнала объект"


def test_single_false_detection_does_not_jerk_the_frame():
    """Одиночная ошибка детектора не утаскивает рамку.

    Предел скорости лечит именно это: ложное срабатывание на другом краю
    кадра иначе дало бы рывок через весь экран.
    """
    positions = [640.0] * 20 + [50.0] + [640.0] * 20
    track = smooth(positions, times(41), WIDTH)

    assert abs(track.x[20] - track.x[19]) < WIDTH * 0.2, "рывок на ложной находке"


def test_missing_detections_hold_the_frame():
    """Пропуск детекции оставляет рамку на месте, а не отправляет в начало.

    Пропуски — обычное дело, и дёргать рамку из-за них нельзя.
    """
    positions = [800.0] * 10 + [None] * 10 + [800.0] * 10
    track = smooth(positions, times(30), WIDTH)

    held = track.x[10:20]
    assert np.allclose(held, held[0]), "рамка поехала на пропуске"


def test_starts_at_the_object_not_at_the_centre():
    """Ролик не начинается с проезда от середины кадра к объекту."""
    track = smooth([200.0] * 10, times(10), WIDTH)
    assert track.x[0] == pytest.approx(200.0)


def test_all_detections_missing_is_safe():
    """Ничего не нашлось — рамка стоит в центре, а не падает."""
    track = smooth([None] * 10, times(10), WIDTH)
    assert np.allclose(track.x, WIDTH / 2)


def test_speed_limit_is_per_second_not_per_frame():
    """Предел скорости считается по времени, а не по номеру кадра.

    Иначе при редкой детекции рамка ползла бы неоправданно медленно.
    """
    far = [640.0, 1200.0]
    fast = smooth(far, [0.0, 0.05], WIDTH, smoothing=1.0)
    slow = smooth(far, [0.0, 1.0], WIDTH, smoothing=1.0)

    assert slow.x[1] > fast.x[1], "за большее время рамка должна успеть дальше"


def test_crop_is_clamped_to_the_frame():
    """Рамка не уезжает за край: там чёрная полоса, а ffmpeg откажет."""
    track = Track(np.array([50.0, 1250.0]), np.array([0.0, 1.0]))

    assert crop_centre(track, 0.0, 600.0, SOURCE) == 0.0
    assert crop_centre(track, 1.0, 600.0, SOURCE) == SOURCE - 600.0


def test_interpolation_between_detections():
    """Детекция реже кадров, а рамку надо ставить в каждом."""
    track = Track(np.array([0.0, 100.0]), np.array([0.0, 1.0]))
    assert track.at(0.5) == pytest.approx(50.0)


def test_mismatched_input_is_rejected():
    assert smooth([1.0, 2.0], [0.0], WIDTH).x.size == 0
