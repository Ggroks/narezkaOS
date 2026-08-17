"""Таймкоды длинной нарезки."""

import pytest

from narezka.core.chapters import Chapter, as_text, build, timecode


def test_timecode_hides_hours_when_there_are_none():
    """«5:00» для короткого ролика, «1:05:00» для длинного."""
    assert timecode(300) == "5:00"
    assert timecode(3725) == "1:02:05"


def test_minutes_keep_the_leading_zero():
    """Без ведущего нуля часть площадок список не распознаёт."""
    assert timecode(3665) == "1:01:05"


def test_negative_time_is_clamped():
    assert timecode(-10) == "0:00"


def test_chapters_follow_the_assembled_order():
    """Начало главы — это её место в готовом ролике, а не в записи."""
    chapters = build([(1000, 1060), (0, 240)], {0: "первая", 1: "вторая"})

    assert chapters[0].at == 0.0
    assert chapters[1].at == pytest.approx(60.0)


def test_first_chapter_always_starts_at_zero():
    """Площадки отвергают список, где первый таймкод не 0:00.

    Оглавление тогда не показывается вовсе — молча.
    """
    chapters = build([(0, 60), (100, 340)], {1: "вторая"})
    assert chapters[0].at == 0.0


def test_unnamed_piece_joins_the_previous_chapter():
    """Кусок без названия не получает пустую главу: «—» хуже её отсутствия."""
    chapters = build([(0, 60), (100, 340), (400, 700)], {0: "первая", 2: "третья"})

    assert [c.title for c in chapters] == ["первая", "третья"]


def test_chapters_too_close_are_merged():
    """Список из тридцати строк по двадцать секунд бесполезен.

    Площадки такое оглавление и не показывают.
    """
    pieces = [(i * 100, i * 100 + 20) for i in range(6)]
    titles = {i: f"кусок {i}" for i in range(6)}

    chapters = build(pieces, titles, min_gap=60.0)

    assert len(chapters) < 6
    for previous, current in zip(chapters, chapters[1:], strict=False):
        assert current.at - previous.at >= 60.0


def test_text_shows_ranges_not_points():
    """Промежуток нагляднее точки: сразу видно, сколько длится кусок."""
    text = as_text([Chapter(0, "раз"), Chapter(60, "два")], 300)

    assert text.splitlines()[0] == "0:00-1:00 — раз"
    assert text.splitlines()[1] == "1:00-5:00 — два"


def test_empty_input_gives_empty_text():
    assert as_text([], 300) == ""
    assert build([], {}) == []
