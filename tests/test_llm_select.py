"""Тесты стадии отбора моментов (BAZA.md §11, §14, §26).

Сеть не трогается: проверяется разбор ответа и подготовка контекста —
то, что ломается от смены модели, а не от отсутствия интернета.
"""

from __future__ import annotations

import pytest

from narezka.stages.llm_select import parse_reply, transcript_lines


def segment(start: float, end: float, text: str, suspect: bool = False) -> dict:
    return {"start": start, "end": end, "text": text, "suspect": suspect}


TRANSCRIPT = {
    "segments": [
        segment(0.0, 5.0, "начало записи"),
        segment(10.0, 15.0, "внутри окна"),
        segment(16.0, 20.0, "тоже внутри"),
        segment(100.0, 105.0, "далеко за окном"),
    ]
}


# --- контекст для модели ---------------------------------------------------


def test_only_overlapping_lines_are_taken() -> None:
    lines = transcript_lines(TRANSCRIPT, 9.0, 21.0)
    assert [line["text"] for line in lines] == ["внутри окна", "тоже внутри"]


def test_partially_overlapping_line_is_included() -> None:
    """Реплика, начавшаяся до окна, нужна: §14 требует отодвинуть начало
    к завязке, а двигать некуда, если реплики до окна не показаны."""
    lines = transcript_lines(TRANSCRIPT, 3.0, 12.0)
    assert "начало записи" in [line["text"] for line in lines]


def test_suspect_segments_are_excluded() -> None:
    """Галлюцинации не должны попадать в контекст отбора (§57)."""
    transcript = {"segments": [segment(10.0, 15.0, "Субтитры сделал DimaTorzok", suspect=True)]}
    assert transcript_lines(transcript, 0.0, 100.0) == []


def test_lines_keep_timestamps() -> None:
    """Без меток модель не может назвать границу числом."""
    assert transcript_lines(TRANSCRIPT, 9.0, 21.0)[0]["start"] == 10.0


def test_empty_transcript_is_not_an_error() -> None:
    assert transcript_lines({}, 0.0, 10.0) == []


# --- разбор ответа ---------------------------------------------------------


def test_plain_json_is_parsed() -> None:
    clips = parse_reply('{"clips": [{"index": 0, "start": 1.0}]}')
    assert clips == [{"index": 0, "start": 1.0}]


def test_markdown_fence_is_stripped() -> None:
    """Часть моделей оборачивает ответ в заборчик, несмотря на схему.
    Снять его здесь дешевле, чем потерять весь пакет."""
    clips = parse_reply('```json\n{"clips": [{"index": 2}]}\n```')
    assert clips == [{"index": 2}]


def test_bare_array_is_accepted() -> None:
    """Некоторые модели возвращают массив без обёртки."""
    assert parse_reply('[{"index": 1}]') == [{"index": 1}]


def test_non_object_entries_are_dropped() -> None:
    assert parse_reply('{"clips": [{"index": 0}, "мусор", 5]}') == [{"index": 0}]


def test_broken_json_raises_with_explanation() -> None:
    with pytest.raises(ValueError, match="JSON"):
        parse_reply("совсем не json")


def test_missing_clips_key_raises() -> None:
    with pytest.raises(ValueError, match="clips"):
        parse_reply('{"результат": "готово"}')


def test_empty_reply_raises() -> None:
    with pytest.raises(ValueError):
        parse_reply("")
