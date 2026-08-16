"""Бэкенд YOLOX: подавление дубликатов и честность о доступности."""

import numpy as np
import pytest

from narezka.core import detectors


def test_overlapping_boxes_collapse_to_one():
    """Сетка YOLOX даёт десятки находок на объект — остаётся одна.

    Замер на кадре стрима: 25 прямоугольников на одного человека. Без
    подавления вызывающий код видит толпу там, где сидит один стример.
    """
    boxes = [(100, 100, 50, 80, 0.9), (102, 101, 49, 79, 0.7), (104, 103, 51, 81, 0.6)]
    assert len(detectors._suppress_overlaps(boxes)) == 1


def test_separate_objects_are_kept():
    """Непересекающиеся объекты остаются оба — подавление не жадное."""
    boxes = [(0, 0, 40, 40, 0.9), (500, 500, 40, 40, 0.8)]
    assert len(detectors._suppress_overlaps(boxes)) == 2


def test_strongest_box_wins():
    """Из группы дубликатов остаётся самый уверенный, а не первый попавшийся."""
    boxes = [(100, 100, 50, 80, 0.4), (101, 101, 50, 80, 0.95)]
    assert detectors._suppress_overlaps(boxes)[0][4] == pytest.approx(0.95)


def test_missing_weights_are_reported_not_hidden():
    """Нет файла модели — сказать где его ждут, а не молча отключиться."""
    reason = detectors.available("yolox")
    if reason is not None:
        assert str(detectors.MODELS_DIR) in reason


def test_unknown_backend_is_rejected():
    with pytest.raises(detectors.DetectorError):
        detectors.create("несуществующий")
