"""Сборка длинной компиляции: порядок решает, досмотрят ли двадцать минут."""

import pytest

from narezka.core.compilation import arrange, select


def clip(index: int, start: float, length: float, score: float) -> dict:
    return {
        "index": index, "start": start, "end": start + length,
        "duration": length, "interest_score": score,
    }


def test_selection_takes_the_strongest():
    """В компиляцию попадает лучшее, а не первое попавшееся."""
    clips = [clip(0, 0, 60, 0.3), clip(1, 100, 60, 0.9), clip(2, 200, 60, 0.6)]
    chosen = select(clips, 120)

    assert {c["index"] for c in chosen} == {1, 2}


def test_selection_does_not_cut_a_moment_in_half():
    """Лучше выйти за длительность, чем оборвать реплику.

    Оборванная фраза заметнее лишней минуты.
    """
    clips = [clip(i, i * 100, 70, 0.9 - i * 0.1) for i in range(5)]
    chosen = select(clips, 100)

    assert all(c["duration"] == 70 for c in chosen), "моменты не режутся"
    assert sum(c["duration"] for c in chosen) >= 70


def test_best_moment_goes_to_the_climax_not_the_hook():
    """Сильнейшее — ближе к концу, ради него смотрят.

    Поставив лучшее первым, дальше идём только вниз, и зритель уходит
    ровно тогда, когда стало скучнее.
    """
    clips = [clip(i, i * 100, 60, s) for i, s in enumerate([0.5, 0.99, 0.7, 0.6, 0.4])]
    result = arrange(clips, 600)

    roles = {part.role: part.clip["index"] for part in result.parts}
    assert roles["climax"] == 1, "лучший момент должен быть кульминацией"
    assert result.parts[0].role == "hook"
    assert result.parts[0].clip["index"] != 1


def test_middle_keeps_chronology():
    """Середина идёт в хронологическом порядке — это и есть контекст.

    Если стример получил задание, выполнил его и праздновал, перемешать
    значит показать праздник до задания.
    """
    clips = [clip(i, i * 100, 60, 0.5 + (i % 3) * 0.05) for i in range(7)]
    result = arrange(clips, 3000)

    body = [p.clip["start"] for p in result.parts if p.role == "body"]
    assert body == sorted(body), f"середина перемешана: {body}"


def test_ending_is_the_calmest():
    """Ролик должен завершиться, а не оборваться на крике."""
    clips = [clip(i, i * 100, 60, s) for i, s in enumerate([0.9, 0.8, 0.7, 0.2, 0.6])]
    result = arrange(clips, 3000)

    ending = next(p for p in result.parts if p.role == "ending")
    assert ending.clip["index"] == 3, "концовкой должен стать спокойный момент"
    assert result.parts[-1].role == "ending"


def test_positions_follow_one_another():
    """Места в компиляции идут подряд, без дыр и наложений."""
    clips = [clip(i, i * 100, 40, 0.5 + i * 0.05) for i in range(6)]
    result = arrange(clips, 3000)

    at = 0.0
    for part in result.parts:
        assert part.at == pytest.approx(at)
        at += part.duration
    assert result.duration == pytest.approx(at)


def test_single_moment_is_the_climax():
    """Один момент — это и есть кульминация, зацепку строить не из чего."""
    result = arrange([clip(0, 0, 60, 0.8)], 600)
    assert [p.role for p in result.parts] == ["climax"]


def test_no_clips_gives_empty_compilation():
    assert arrange([], 600).parts == []


def test_zero_target_selects_nothing():
    assert select([clip(0, 0, 60, 0.9)], 0) == []


def test_unscored_clips_do_not_crash():
    """Без оценки модели момент всё равно можно поставить в компиляцию."""
    raw = [{"index": 0, "start": 0, "end": 60, "duration": 60}]
    assert len(arrange(raw, 600).parts) == 1
