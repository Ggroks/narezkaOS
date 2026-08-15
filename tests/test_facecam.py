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


# --- область проигрываемого контента ---------------------------------------


def synthetic_frames(count=6, width=320, height=180):
    """Кадры, где движется только прямоугольник в центре — как проигрываемое
    видео на неподвижной странице."""
    frames = []
    rng = np.random.default_rng(7)
    for _ in range(count):
        frame = np.full((height, width, 3), 40, dtype=np.uint8)
        frame[60:140, 100:240] = rng.integers(0, 255, (80, 140, 3), dtype=np.uint8)
        frames.append(frame)
    return frames


def test_content_region_finds_the_moving_rectangle() -> None:
    """Признак — движение: у проигрываемого видео оно непрерывное,
    а интерфейс браузера неподвижен."""
    rect = facecam.detect_content(synthetic_frames())
    assert rect is not None
    assert 80 <= rect.x <= 120 and 40 <= rect.y <= 80
    assert 110 <= rect.width <= 180 and 50 <= rect.height <= 120


def test_static_frames_have_no_content_region() -> None:
    frames = [np.full((180, 320, 3), 40, dtype=np.uint8) for _ in range(6)]
    assert facecam.detect_content(frames) is None


def test_single_frame_cannot_show_motion() -> None:
    assert facecam.detect_content(synthetic_frames(count=1)) is None


def test_webcam_area_is_excluded() -> None:
    """Вебка тоже подвижна: без исключения победила бы она сама,
    и нижняя полоса показала бы лицо второй раз."""
    frames = synthetic_frames()
    rng = np.random.default_rng(3)
    for frame in frames:
        frame[0:50, 0:70] = rng.integers(0, 255, (50, 70, 3), dtype=np.uint8)

    rect = facecam.detect_content(frames, exclude=facecam.Rect(0, 0, 70, 50))
    assert rect is not None
    assert rect.x >= 70 or rect.y >= 50


def test_split_uses_the_content_region() -> None:
    """Без области нижняя полоса режется по центру и захватывает интерфейс."""
    without = plan_split(1280, 720, 1080, 1920, (0, 0, 281, 210))
    with_content = plan_split(
        1280, 720, 1080, 1920, (0, 0, 281, 210), content=(320, 180, 640, 360)
    )
    assert with_content.main_crop != without.main_crop
    x, y, w, h = with_content.main_crop
    assert x >= 318 and y >= 178
    assert x + w <= 320 + 640 + 2


def test_rect_serializes_to_plain_json() -> None:
    """OpenCV возвращает numpy.int32, и json на нём падает уже при записи
    артефакта — то есть после того, как вся работа стадии проделана.
    Прогон выглядел зависшим, хотя стадия падала.
    """
    import json

    rect = facecam.detect_content(synthetic_frames())
    assert rect is not None
    # Не должно бросать исключение.
    assert json.dumps(rect.as_dict())


def test_facecam_result_serializes() -> None:
    import json

    result = facecam.detect(FakeDetector([(20, 30, 60, 0.95)]), frames())
    assert result is not None
    assert json.dumps(result.as_dict())


def test_narrow_strip_is_not_content() -> None:
    """Реальный случай с записи: областью выходила полоса 184x516 — лента
    сообщений, а не видео. Неверная область хуже её отсутствия: без неё
    нижняя полоса режется по разумному умолчанию, а с ней — по ленте чата.
    """
    frames = []
    rng = np.random.default_rng(11)
    for _ in range(6):
        frame = np.full((180, 320, 3), 40, dtype=np.uint8)
        frame[20:160, 260:300] = rng.integers(0, 255, (140, 40, 3), dtype=np.uint8)
        frames.append(frame)
    assert facecam.detect_content(frames) is None


def test_wide_video_region_is_accepted() -> None:
    rect = facecam.detect_content(synthetic_frames())
    assert rect is not None
    assert 1.1 <= rect.width / rect.height <= 2.6
