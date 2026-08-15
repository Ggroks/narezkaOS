"""Тесты отчёта «признаки → результат» (BAZA.md §63, §54).

Главная опасность этого модуля — самообман: на четырёх клипах любая
корреляция выйдет высокой и ничего не будет значить. Проверяется, что объём
выборки всегда рядом с числом, а недостоверное помечено.
"""

from __future__ import annotations

import pytest

from narezka.core import feedback


def row(semantic: float, views: int | None = None, **extra) -> dict:
    base = {
        "factors_snapshot": {"semantic": semantic, "emotion": 0.5, "visual": None},
        "views": views,
        "interest_score": semantic,
    }
    base.update(extra)
    return base


# --- корреляция ------------------------------------------------------------


def test_perfect_positive_relation() -> None:
    assert feedback.pearson([1, 2, 3], [10, 20, 30]) == 1.0


def test_perfect_negative_relation() -> None:
    assert feedback.pearson([1, 2, 3], [30, 20, 10]) == -1.0


def test_constant_series_has_no_relation() -> None:
    """Постоянный ряд — не ошибка счёта, а отсутствие связи."""
    assert feedback.pearson([1, 1, 1], [10, 20, 30]) is None


def test_single_point_cannot_be_correlated() -> None:
    assert feedback.pearson([1], [10]) is None


def test_mismatched_lengths_are_rejected() -> None:
    assert feedback.pearson([1, 2], [10]) is None


# --- отчёт по факторам -----------------------------------------------------


def test_factor_correlation_is_computed() -> None:
    rows = [row(0.2, 100), row(0.5, 400), row(0.9, 900)]
    result = feedback.factor_correlations(rows)
    assert result["factors"]["semantic"]["correlation"] > 0.9
    assert result["factors"]["semantic"]["sample"] == 3


def test_small_sample_is_marked_unreliable() -> None:
    """§54: число без объёма выборки — приглашение к самообману."""
    result = feedback.factor_correlations([row(0.2, 100), row(0.9, 900)])
    assert result["reliable"] is False
    assert result["min_sample"] == feedback.MIN_SAMPLE


def test_large_sample_is_marked_reliable() -> None:
    rows = [row(i / 10, i * 100) for i in range(1, feedback.MIN_SAMPLE + 1)]
    assert feedback.factor_correlations(rows)["reliable"] is True


def test_unmeasured_factors_are_skipped() -> None:
    """Визуальный фактор всегда None — его не должно быть в отчёте."""
    result = feedback.factor_correlations([row(0.2, 100), row(0.9, 900)])
    assert "visual" not in result["factors"]


def test_rows_without_metrics_are_ignored() -> None:
    result = feedback.factor_correlations([row(0.2, 100), row(0.9, None)])
    assert result["sample"] == 1


def test_status_distinguishes_no_data_from_too_few_points() -> None:
    """«Метрик нет» и «метрики есть, но точка одна» требуют разных действий."""
    assert feedback.correlation_status(feedback.factor_correlations([])) == "no_data"
    assert feedback.correlation_status(feedback.factor_correlations([row(0.5, 100)])) == "single_point"
    assert feedback.correlation_status(
        feedback.factor_correlations([row(0.2, 100), row(0.9, 900)])
    ) == "ok"


# --- согласие с человеком --------------------------------------------------


def test_gap_shows_agreement_with_the_human() -> None:
    rows = [
        row(0.9, human_verdict="accept"),
        row(0.8, human_verdict="accept"),
        row(0.2, human_verdict="reject"),
    ]
    result = feedback.verdict_split(rows)
    assert result["gap"] == pytest.approx(0.65, abs=0.01)


def test_no_verdicts_gives_no_gap() -> None:
    assert feedback.verdict_split([row(0.5)])["gap"] is None


def test_verdict_split_needs_both_sides_to_be_reliable() -> None:
    rows = [row(0.9, human_verdict="accept") for _ in range(5)]
    assert feedback.verdict_split(rows)["reliable"] is False


# --- систематический сдвиг границ ------------------------------------------


def test_bounds_bias_averages_the_correction() -> None:
    """Если человек раз за разом двигает начало в одну сторону, дело
    не в отдельных клипах, а в том, как стадия ищет завязку."""
    rows = [
        {"bounds_shift_start": 2.0, "bounds_shift_end": -1.0},
        {"bounds_shift_start": 4.0, "bounds_shift_end": -3.0},
    ]
    result = feedback.bounds_bias(rows)
    assert result["mean_start_shift"] == 3.0
    assert result["mean_end_shift"] == -2.0


def test_bounds_bias_without_data() -> None:
    result = feedback.bounds_bias([{"bounds_shift_start": None}])
    assert result["mean_start_shift"] is None
    assert result["reliable"] is False


def test_full_report_counts_everything() -> None:
    rows = [
        row(0.9, 500, published_at="2026-08-01", measured_at="2026-08-08"),
        row(0.2, None, published_at="2026-08-02"),
    ]
    result = feedback.report(rows)
    assert result == {
        "clips": 2, "published": 2, "measured": 1,
        "correlations": result["correlations"],
        "verdicts": result["verdicts"],
        "bounds": result["bounds"],
    }
