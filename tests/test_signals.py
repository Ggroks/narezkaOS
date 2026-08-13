"""Тесты дешёвых сигналов отбора кандидатов (BAZA.md §11).

Проверяется в первую очередь устойчивость: сигнал считается относительно
самого материала, и ошибка здесь не падает с исключением, а тихо возвращает
бессмысленные окна.
"""

from __future__ import annotations

import numpy as np
import pytest

from narezka.core.signals import (
    deduplicate,
    find_peaks,
    limit_coverage,
    loudness_track,
    overlap_ratio,
    robust_z,
    snap_to_segments,
    speech_density,
)


def tone(seconds: float, rate: int = 16000, amplitude: float = 0.5) -> np.ndarray:
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


# --- громкость -------------------------------------------------------------


def test_loudness_track_window_count() -> None:
    track = loudness_track(tone(5.0), 16000, window_seconds=1.0)
    assert track.count == 5
    assert track.time_of(3) == 3.0


def test_louder_signal_gives_higher_db() -> None:
    quiet = loudness_track(tone(2.0, amplitude=0.05), 16000).rms_db.mean()
    loud = loudness_track(tone(2.0, amplitude=0.8), 16000).rms_db.mean()
    assert loud > quiet + 10


def test_silence_does_not_produce_infinity() -> None:
    track = loudness_track(np.zeros(16000 * 2, dtype=np.float32), 16000)
    assert np.all(np.isfinite(track.rms_db))


def test_too_short_audio_gives_empty_track() -> None:
    track = loudness_track(np.zeros(100, dtype=np.float32), 16000, window_seconds=1.0)
    assert track.count == 0


# --- устойчивая нормализация ----------------------------------------------


def test_robust_z_marks_outlier() -> None:
    values = np.array([1.0] * 20 + [10.0], dtype=np.float32)
    z = robust_z(values)
    assert z[-1] > 3
    assert abs(z[0]) < 1


def test_robust_z_survives_constant_input() -> None:
    """Ровный сигнал не должен давать деления на ноль."""
    z = robust_z(np.full(10, 5.0, dtype=np.float32))
    assert np.all(z == 0)


def test_robust_z_is_not_dragged_by_outliers() -> None:
    """Медиана вместо среднего: один крик не должен сдвигать точку отсчёта."""
    base = np.array([1.0] * 30, dtype=np.float32)
    with_outlier = np.concatenate([base, np.array([100.0], dtype=np.float32)])
    assert abs(float(robust_z(with_outlier)[0])) < 1.0


def test_robust_z_handles_empty() -> None:
    assert len(robust_z(np.zeros(0, dtype=np.float32))) == 0


# --- плотность речи --------------------------------------------------------


def test_speech_density_counts_words_per_window() -> None:
    segments = [
        {"words": [{"start": 0.1}, {"start": 0.5}, {"start": 0.9}]},
        {"words": [{"start": 2.2}]},
    ]
    density = speech_density(segments, total_windows=4, window_seconds=1.0)
    assert density[0] == 3.0
    assert density[1] == 0.0
    assert density[2] == 1.0


def test_speech_density_ignores_words_beyond_track() -> None:
    segments = [{"words": [{"start": 99.0}]}]
    density = speech_density(segments, total_windows=3, window_seconds=1.0)
    assert density.sum() == 0.0


def test_speech_density_tolerates_segments_without_words() -> None:
    density = speech_density([{"text": "нет разметки"}], total_windows=2, window_seconds=1.0)
    assert density.sum() == 0.0


# --- поиск пиков -----------------------------------------------------------


def test_find_peaks_respects_threshold() -> None:
    score = np.array([0.1, 0.2, 0.1], dtype=np.float32)
    assert find_peaks(score, min_score=1.0, min_gap=1) == []


def test_find_peaks_enforces_gap() -> None:
    """Один длинный всплеск не должен дать десяток кандидатов."""
    score = np.zeros(40, dtype=np.float32)
    score[10:14] = 5.0
    peaks = find_peaks(score, min_score=1.0, min_gap=20)
    assert len(peaks) == 1


def test_find_peaks_keeps_distant_events() -> None:
    score = np.zeros(60, dtype=np.float32)
    score[5] = 4.0
    score[40] = 3.0
    assert find_peaks(score, min_score=1.0, min_gap=20) == [5, 40]


def test_find_peaks_on_empty() -> None:
    assert find_peaks(np.zeros(0, dtype=np.float32), min_score=1.0, min_gap=5) == []


# --- выравнивание границ ---------------------------------------------------


SEGMENTS = [
    {"start": 0.0, "end": 5.0},
    {"start": 5.0, "end": 12.0},
    {"start": 12.0, "end": 20.0},
]


def test_snap_expands_to_segment_bounds() -> None:
    """§15: клип не должен обрываться посреди фразы."""
    assert snap_to_segments(6.0, 13.0, SEGMENTS) == (5.0, 20.0)


def test_snap_returns_none_outside_speech() -> None:
    assert snap_to_segments(50.0, 60.0, SEGMENTS) is None


# --- перекрытие и дедупликация --------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ((0.0, 10.0), (20.0, 30.0), 0.0),
        ((0.0, 10.0), (5.0, 15.0), 0.5),
        ((0.0, 10.0), (0.0, 10.0), 1.0),
        ((0.0, 100.0), (10.0, 20.0), 1.0),  # вложенный интервал перекрыт целиком
    ],
)
def test_overlap_ratio(a: tuple[float, float], b: tuple[float, float], expected: float) -> None:
    assert overlap_ratio(a, b) == pytest.approx(expected)


def test_deduplicate_keeps_stronger_of_overlapping() -> None:
    candidates = [
        {"start": 0.0, "end": 60.0, "provisional_score": 1.0},
        {"start": 10.0, "end": 70.0, "provisional_score": 2.0},
        {"start": 200.0, "end": 260.0, "provisional_score": 0.5},
    ]
    kept = deduplicate(candidates, max_overlap=0.5)
    assert [c["provisional_score"] for c in kept] == [2.0, 0.5]


def test_deduplicate_preserves_time_order() -> None:
    candidates = [
        {"start": 300.0, "end": 360.0, "provisional_score": 3.0},
        {"start": 0.0, "end": 60.0, "provisional_score": 1.0},
    ]
    kept = deduplicate(candidates, max_overlap=0.5)
    assert [c["start"] for c in kept] == [0.0, 300.0]


# --- потолок покрытия ------------------------------------------------------


def test_coverage_ceiling_keeps_strongest() -> None:
    candidates = [
        {"start": 0.0, "end": 60.0, "provisional_score": 1.0},
        {"start": 100.0, "end": 160.0, "provisional_score": 3.0},
        {"start": 200.0, "end": 260.0, "provisional_score": 2.0},
    ]
    kept, hit = limit_coverage(candidates, total_seconds=400.0, max_coverage=0.3)
    assert hit is True
    assert [c["provisional_score"] for c in kept] == [3.0, 2.0]


def test_coverage_ceiling_not_hit_when_material_is_long() -> None:
    candidates = [{"start": 0.0, "end": 60.0, "provisional_score": 1.0}]
    kept, hit = limit_coverage(candidates, total_seconds=3600.0, max_coverage=0.3)
    assert hit is False
    assert len(kept) == 1


def test_coverage_ceiling_keeps_at_least_one() -> None:
    """Даже если единственный кандидат длиннее бюджета, он не теряется."""
    candidates = [{"start": 0.0, "end": 90.0, "provisional_score": 1.0}]
    kept, _ = limit_coverage(candidates, total_seconds=100.0, max_coverage=0.1)
    assert len(kept) == 1


def test_coverage_ceiling_on_empty() -> None:
    kept, hit = limit_coverage([], total_seconds=100.0, max_coverage=0.3)
    assert kept == []
    assert hit is False
