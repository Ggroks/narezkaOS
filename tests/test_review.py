"""Тесты разметки кандидатов человеком (BAZA.md §35, §63).

Главное, что здесь проверяется, — что накопленные метки останутся пригодными
для обучения через месяцы: признаки заморожены, исходные границы не затёрты.
"""

from __future__ import annotations

import pytest

from narezka.core import review


def candidate(start: float = 10.0, end: float = 40.0, **extra) -> dict:
    base = {
        "start": start,
        "end": end,
        "provisional_score": 2.4,
        "signals": {"loudness_z": 2.7, "words_per_second": 4.0},
        "text": "какая-то реплика",
    }
    base.update(extra)
    return base


def test_new_review_is_empty() -> None:
    assert review.stats(review.merge([], review.empty()))["total"] == 0


def test_decision_is_recorded() -> None:
    state = review.record(review.empty(), index=0, candidate=candidate(), verdict="accept")
    merged = review.merge([candidate()], state)
    assert merged[0]["verdict"] == "accept"
    assert merged[0]["index"] == 0


def test_snapshot_is_frozen_at_decision_time() -> None:
    """§63: через месяц должно быть видно, на какие числа смотрел человек.

    Кандидат пересчитывается при каждом изменении стадии. Если снимок
    не заморозить, метка «годится» перестанет быть привязана к признакам
    и обучать на ней будет нечему.
    """
    original = candidate(provisional_score=2.4)
    state = review.record(review.empty(), index=0, candidate=original, verdict="accept")

    stored = review.by_index(state)[0]["snapshot"]
    assert stored["provisional_score"] == 2.4

    # Стадия пересчиталась, числа поехали — снимок обязан остаться прежним.
    recalculated = candidate(provisional_score=9.9)
    state = review.record(state, index=0, candidate=recalculated, start=12.0)
    assert review.by_index(state)[0]["snapshot"]["provisional_score"] == 2.4


def test_snapshot_is_a_copy_not_a_reference() -> None:
    """Изменение исходного словаря не должно задним числом менять снимок."""
    source = candidate()
    state = review.record(review.empty(), index=0, candidate=source, verdict="accept")
    source["signals"]["loudness_z"] = 0.0
    assert review.by_index(state)[0]["snapshot"]["signals"]["loudness_z"] == 2.7


def test_original_bounds_survive_editing() -> None:
    """Насколько человек подвинул границу — отдельный сигнал (§63)."""
    state = review.record(review.empty(), index=0, candidate=candidate(10.0, 40.0), start=8.0)
    state = review.record(state, index=0, candidate=candidate(10.0, 40.0), end=45.0)

    entry = review.by_index(state)[0]
    assert entry["start"] == 8.0
    assert entry["end"] == 45.0
    assert entry["original"] == {"start": 10.0, "end": 40.0}


def test_editing_bounds_keeps_the_verdict() -> None:
    state = review.record(review.empty(), index=0, candidate=candidate(), verdict="accept")
    state = review.record(state, index=0, candidate=candidate(), start=12.0)
    assert review.by_index(state)[0]["verdict"] == "accept"


def test_verdict_change_keeps_edited_bounds() -> None:
    state = review.record(review.empty(), index=0, candidate=candidate(), start=12.0)
    state = review.record(state, index=0, candidate=candidate(), verdict="reject")
    entry = review.by_index(state)[0]
    assert entry["verdict"] == "reject"
    assert entry["start"] == 12.0


def test_merge_returns_edited_bounds() -> None:
    state = review.record(review.empty(), index=0, candidate=candidate(10.0, 40.0), start=15.0)
    merged = review.merge([candidate(10.0, 40.0)], state)
    assert merged[0]["start"] == 15.0
    assert merged[0]["duration"] == 25.0
    assert merged[0]["edited"] is True


def test_tiny_nudge_is_not_an_edit() -> None:
    """Округление при перетаскивании не должно считаться правкой."""
    state = review.record(review.empty(), index=0, candidate=candidate(10.0, 40.0), start=10.01)
    assert review.merge([candidate(10.0, 40.0)], state)[0]["edited"] is False


def test_backwards_bounds_are_rejected() -> None:
    with pytest.raises(ValueError):
        review.record(review.empty(), index=0, candidate=candidate(), start=50.0, end=40.0)


def test_forget_clears_the_decision() -> None:
    state = review.record(review.empty(), index=0, candidate=candidate(), verdict="accept")
    state = review.forget(state, 0)
    assert review.merge([candidate()], state)[0]["verdict"] is None


def test_decisions_stay_sorted_by_index() -> None:
    state = review.empty()
    for index in (2, 0, 1):
        state = review.record(state, index=index, candidate=candidate(), verdict="accept")
    assert [d["index"] for d in state["decisions"]] == [0, 1, 2]


def test_stats_count_every_bucket() -> None:
    state = review.empty()
    state = review.record(state, index=0, candidate=candidate(), verdict="accept")
    state = review.record(state, index=1, candidate=candidate(), verdict="reject")
    state = review.record(state, index=2, candidate=candidate(10.0, 40.0), start=15.0)

    result = review.stats(review.merge([candidate()] * 4, state))
    assert result == {"total": 4, "accepted": 1, "rejected": 1, "undecided": 2, "edited": 1}


def test_decision_for_missing_candidate_does_not_break_merge() -> None:
    """Кандидатов стало меньше после пересчёта — решение по исчезнувшему
    не должно ронять экран."""
    state = review.record(review.empty(), index=5, candidate=candidate(), verdict="accept")
    merged = review.merge([candidate()], state)
    assert len(merged) == 1
    assert merged[0]["verdict"] is None
