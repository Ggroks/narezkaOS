"""Смена сцен: адаптивный порог и минимальная длина."""

import numpy as np
import pytest

from narezka.core.scenes import SceneReport, detect, find_cuts, frame_difference


def frames(pattern: list[tuple[int, int]]) -> np.ndarray:
    """Кадры: пары (яркость, сколько кадров)."""
    return np.concatenate([np.full((n, 8, 8), v, dtype=np.uint8) for v, n in pattern])


def test_static_video_has_no_cuts():
    """Неподвижная картинка не должна порождать сцен из ничего."""
    assert detect(frames([(100, 30)]), 0.5).count == 0


def test_hard_cut_is_found():
    """Резкая смена плана находится."""
    report = detect(frames([(30, 20), (220, 20)]), 0.5)
    assert report.count == 1
    assert report.cuts[0] == pytest.approx(10.0, abs=0.6)


def test_gradual_change_is_not_a_cut():
    """Плавное изменение — не склейка.

    Постепенное осветление кадра меняет картинку так же сильно, но
    происходит равномерно, и адаптивный порог его не выделяет.
    """
    gradual = np.stack([np.full((8, 8), v, dtype=np.uint8) for v in range(0, 240, 4)])
    assert detect(gradual, 0.5).count == 0


def test_minimum_scene_length_is_enforced():
    """Быстрая нарезка не даёт десятка сцен подряд.

    Без ограничения одна склейка на границе двух кадров давала бы две
    смены вместо одной.
    """
    rapid = frames([(20, 12), (230, 1), (20, 1), (230, 1), (20, 12)])
    report = detect(rapid, 0.5, min_scene=3.0)

    gaps = [b - a for a, b in zip(report.cuts, report.cuts[1:], strict=False)]
    assert all(g >= 3.0 for g in gaps), f"сцены ближе минимума: {report.cuts}"


def test_threshold_adapts_to_noisy_material():
    """Порог подстраивается под материал.

    На шумной записи постоянная тряска не должна читаться как склейки —
    иначе на геймплее сигнал даст тысячи ложных срабатываний.
    """
    rng = np.random.default_rng(3)
    noisy = rng.integers(90, 160, size=(80, 8, 8), dtype=np.uint8)
    assert detect(noisy, 0.5).count <= 2, "шум не должен читаться как смена планов"


def test_scenes_cover_the_whole_recording():
    """Сцены покрывают запись целиком, без дыр."""
    report = SceneReport(cuts=[10.0, 25.0], difference=np.zeros(0), step=0.5)
    scenes = report.scenes(40.0)

    assert scenes[0][0] == 0.0 and scenes[-1][1] == 40.0
    for (_, end), (start, _) in zip(scenes, scenes[1:], strict=False):
        assert end == start, "между сценами не должно быть разрыва"


def test_nearest_finds_a_cut_within_the_window():
    """Граница клипа подтягивается к склейке, если та рядом."""
    report = SceneReport(cuts=[10.0, 25.0], difference=np.zeros(0), step=0.5)

    assert report.nearest(11.0, window=3.0) == 10.0
    assert report.nearest(18.0, window=3.0) is None, "далёкую склейку не притягиваем"


def test_no_cuts_means_no_nearest():
    report = SceneReport(cuts=[], difference=np.zeros(0), step=0.5)
    assert report.nearest(5.0) is None


def test_single_frame_is_safe():
    assert frame_difference(frames([(100, 1)])).size == 0
    assert find_cuts(np.zeros(0), 0.5) == []


def test_separation_tells_edited_material_from_continuous():
    """Детектор сам сообщает, различает он что-нибудь или нет.

    Замер на своих записях: мультфильм даёт 18x — планы разделены отчётливо;
    стрим 5x — сплошная съёмка без монтажа, и «сцены» там оказываются
    всплесками движения, а не склейками.
    """
    edited = detect(frames([(30, 20), (220, 20), (40, 20)]), 0.5)
    assert edited.separation > 10.0
    assert edited.reliable

    rng = np.random.default_rng(11)
    continuous = detect(rng.integers(90, 160, size=(80, 8, 8), dtype=np.uint8), 0.5)
    assert continuous.separation < 10.0
    assert not continuous.reliable, "на сплошной съёмке доверять нечему"


def test_unreliable_is_not_the_same_as_empty():
    """«Склеек нет» и «различить нельзя» — разные ответы.

    Второй должен быть виден, а не притворяться первым.
    """
    static = detect(frames([(100, 30)]), 0.5)
    assert static.count == 0
    assert not static.reliable
