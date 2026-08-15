"""Тесты поиска вебкамеры и раскладки «сплит» (BAZA.md §17, §61).

Детектор подменяется: сеть и модель тут ни при чём, проверяется логика
выбора окна — что побеждает неподвижное лицо, а не случайное совпадение.
"""

from __future__ import annotations

import numpy as np
import pytest

from narezka.core import facecam
from narezka.core.framing import plan_split


class FakeDetector:
    """Возвращает лицо, когда вырезанная область накрывает заданную точку."""

    def __init__(self, spots):
        # spots: список (x, y, размер, уверенность) в координатах кадра
        self.spots = spots

    def detect(self, image):
        # Область передана уже вырезанной, поэтому положение восстанавливаем
        # по её размеру — этого достаточно для проверки логики выбора.
        h, w = image.shape[:2]
        found = []
        for sx, sy, size, conf in self.spots:
            if sx < w and sy < h:
                found.append((sx, sy, size, size, conf))
        return found


def frames(count=6, width=1280, height=720):
    return [np.zeros((height, width, 3), dtype=np.uint8) for _ in range(count)]


def test_candidates_cover_all_corners() -> None:
    rects = facecam.candidate_rects(1280, 720)
    corners = {(r.x == 0, r.y == 0) for r in rects}
    assert len(corners) == 4, "окна должны предлагаться во всех четырёх углах"


def test_candidates_stay_inside_the_frame() -> None:
    for rect in facecam.candidate_rects(1280, 720):
        assert rect.x >= 0 and rect.y >= 0
        assert rect.right <= 1280 and rect.bottom <= 720


def test_nothing_found_returns_none() -> None:
    assert facecam.detect(FakeDetector([]), frames()) is None


def test_no_frames_returns_none() -> None:
    assert facecam.detect(FakeDetector([(10, 10, 50, 0.9)]), []) is None


def test_face_is_reported_in_frame_coordinates() -> None:
    """Верхняя полоса сплита строится по лицу, а не по найденному окну —
    значит, лицо должно быть в координатах кадра, а не выреза."""
    result = facecam.detect(FakeDetector([(20, 30, 60, 0.95)]), frames())
    assert result is not None
    assert result.face.x >= result.rect.x
    assert result.face.y >= result.rect.y


# --- раскладка «сплит» -----------------------------------------------------


def test_split_fills_the_whole_frame() -> None:
    plan = plan_split(1280, 720, 1080, 1920, (0, 0, 281, 210))
    assert plan.cam_height + plan.main_height == 1920


def test_split_shares_are_bounded() -> None:
    """Слишком узкая полоса делает лицо нечитаемым, слишком широкая
    съедает контент, ради которого ролик и смотрят."""
    low = plan_split(1280, 720, 1080, 1920, (0, 0, 281, 210), top_share=0.01)
    high = plan_split(1280, 720, 1080, 1920, (0, 0, 281, 210), top_share=0.99)
    assert low.cam_height / 1920 >= 0.19
    assert high.cam_height / 1920 <= 0.51


def test_split_frames_by_face_when_given() -> None:
    """Окно ищется грубо, по углам, и захватывает панель браузера.
    Лицо задаёт кадр точно."""
    by_window = plan_split(1280, 720, 1080, 1920, (0, 0, 281, 210))
    by_face = plan_split(1280, 720, 1080, 1920, (0, 0, 281, 210), face=(118, 130, 47, 47))
    assert by_face.cam_crop != by_window.cam_crop
    # Кадр строится вокруг лица, а не от угла экрана.
    assert by_face.cam_crop[0] > 0


def test_split_crops_stay_inside_the_source() -> None:
    plan = plan_split(1280, 720, 1080, 1920, (999, 500, 281, 210), face=(1050, 560, 60, 60))
    for crop in (plan.cam_crop, plan.main_crop):
        x, y, w, h = crop
        assert x >= 0 and y >= 0
        assert x + w <= 1280 and y + h <= 720


def test_split_sizes_are_even() -> None:
    """h264 с yuv420p не принимает нечётные стороны."""
    plan = plan_split(1280, 720, 1080, 1920, (0, 0, 281, 210), face=(118, 130, 47, 47))
    for value in (*plan.cam_crop, *plan.main_crop, plan.cam_height, plan.main_height):
        assert value % 2 == 0


def test_split_is_impossible_without_a_window() -> None:
    assert plan_split(1280, 720, 1080, 1920, (0, 0, 0, 0)) is None


def test_split_filter_stacks_two_bands() -> None:
    from narezka.core.framing import build_split_filter

    plan = plan_split(1280, 720, 1080, 1920, (0, 0, 281, 210))
    chain = build_split_filter(plan, 1080, "00.ass")
    assert "vstack=inputs=2" in chain
    assert chain.count("[0:v]") == 2
    assert "subtitles=00.ass" in chain
