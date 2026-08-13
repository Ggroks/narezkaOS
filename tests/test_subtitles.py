"""Тесты сборки субтитров (BAZA.md §18, §60)."""

from __future__ import annotations

from typing import Any

import pytest

from narezka.core.subtitles import (
    STYLES,
    SubtitleStyle,
    build_ass,
    group_words,
    words_in_range,
    wrap_words,
)


def words(*spec: tuple[str, float, float]) -> list[dict[str, Any]]:
    return [{"word": w, "start": s, "end": e, "probability": 0.9} for w, s, e in spec]


STYLE = STYLES["STYLE_1"]


# --- группировка в реплики ------------------------------------------------


def test_pause_breaks_cue() -> None:
    """Пауза — граница мысли, она важнее лимита по символам."""
    cues = group_words(
        words(("привет", 0.0, 0.4), ("народ", 0.4, 0.9), ("поехали", 3.0, 3.6)),
        STYLE,
    )
    assert len(cues) == 2
    assert cues[0].text == "привет народ"
    assert cues[1].text == "поехали"


def test_long_text_is_split_by_capacity() -> None:
    long = words(*[(f"слово{i}", i * 0.3, i * 0.3 + 0.25) for i in range(30)])
    cues = group_words(long, STYLE)
    capacity = STYLE.max_chars_per_line * STYLE.max_lines
    assert len(cues) > 1
    assert all(len(c.text) <= capacity + 12 for c in cues)


def test_long_duration_is_split() -> None:
    """Реплика не должна висеть на экране дольше разумного."""
    slow = words(*[(f"с{i}", i * 1.0, i * 1.0 + 0.9) for i in range(8)])
    cues = group_words(slow, SubtitleStyle(name="t", max_cue_seconds=2.0, pause_seconds=5.0))
    assert len(cues) > 1


def test_empty_input_gives_no_cues() -> None:
    assert group_words([], STYLE) == []


def test_single_word_becomes_one_cue() -> None:
    cues = group_words(words(("да", 1.0, 1.4)), STYLE)
    assert len(cues) == 1
    assert cues[0].start == 1.0
    assert cues[0].end == 1.4


# --- перенос строк ---------------------------------------------------------


def test_wrap_respects_width() -> None:
    lines = wrap_words(words(*[("абвгде", i * 0.2, i * 0.2 + 0.1) for i in range(6)]), max_chars=14)
    assert len(lines) >= 2
    for line in lines:
        assert len(" ".join(w["word"] for w in line)) <= 14


def test_wrap_keeps_oversized_word_on_own_line() -> None:
    lines = wrap_words(words(("короткое", 0.0, 0.3), ("сверхдлинноеслово", 0.3, 0.9)), max_chars=10)
    assert len(lines) == 2


# --- сборка ASS ------------------------------------------------------------


def test_ass_has_required_sections() -> None:
    ass = build_ass(words(("привет", 0.0, 0.5)), style=STYLE, width=1080, height=1920)
    for section in ("[Script Info]", "[V4+ Styles]", "[Events]"):
        assert section in ass
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass


def test_ass_uses_karaoke_tags() -> None:
    """Подсветка слова делается штатным механизмом ASS, а не покадрово."""
    ass = build_ass(words(("привет", 0.0, 0.5), ("мир", 0.5, 0.9)), style=STYLE, width=1080, height=1920)
    assert "\\kf" in ass
    assert "привет" in ass


def test_bottom_margin_respects_safe_zone() -> None:
    """§60: низ кадра перекрыт интерфейсом платформы, туда текст нельзя."""
    height = 1920
    ass = build_ass(words(("текст", 0.0, 0.5)), style=STYLE, width=1080, height=height)
    style_line = next(line for line in ass.splitlines() if line.startswith("Style: Main"))
    margin_v = int(style_line.split(",")[-2])
    assert margin_v >= height * 0.2


def test_time_offset_rebases_to_clip_start() -> None:
    """Субтитры клипа отсчитываются от его начала, а не от начала записи."""
    ass = build_ass(
        words(("поздно", 100.0, 100.6)), style=STYLE, width=1080, height=1920, time_offset=100.0
    )
    assert "0:00:00.00" in ass


def test_events_before_clip_start_are_dropped() -> None:
    ass = build_ass(
        words(("раньше", 1.0, 2.0)), style=STYLE, width=1080, height=1920, time_offset=100.0
    )
    assert "Dialogue:" not in ass


def test_braces_are_escaped() -> None:
    """Фигурные скобки в тексте не должны становиться разметкой libass."""
    ass = build_ass(words(("{тег}", 0.0, 0.5)), style=STYLE, width=1080, height=1920)
    assert "\\{тег\\}" in ass


def test_empty_words_give_valid_file_without_events() -> None:
    ass = build_ass([], style=STYLE, width=1080, height=1920)
    assert "[Events]" in ass
    assert "Dialogue:" not in ass


@pytest.mark.parametrize("name", ["STYLE_1", "STYLE_2", "STYLE_3"])
def test_all_presets_render(name: str) -> None:
    """§18 требует минимум три пресета — каждый должен собираться."""
    ass = build_ass(words(("проверка", 0.0, 0.6)), style=STYLES[name], width=1080, height=1920)
    assert "Dialogue:" in ass


# --- выборка слов по диапазону --------------------------------------------


SEGMENTS = [
    {"suspect": False, "words": words(("первое", 0.0, 0.5), ("второе", 0.5, 1.0))},
    {"suspect": True, "words": words(("выдумка", 1.2, 1.8))},
    {"suspect": False, "words": words(("третье", 2.0, 2.5))},
]


def test_words_in_range_skips_suspect_segments() -> None:
    """§57: выдуманный текст не должен попасть в кадр."""
    selected = words_in_range(SEGMENTS, 0.0, 5.0)
    assert [w["word"] for w in selected] == ["первое", "второе", "третье"]


def test_words_in_range_filters_by_time() -> None:
    selected = words_in_range(SEGMENTS, 1.9, 3.0)
    assert [w["word"] for w in selected] == ["третье"]


def test_words_in_range_returns_sorted() -> None:
    unordered = [
        {"suspect": False, "words": words(("поздно", 5.0, 5.4))},
        {"suspect": False, "words": words(("рано", 1.0, 1.4))},
    ]
    assert [w["word"] for w in words_in_range(unordered, 0.0, 10.0)] == ["рано", "поздно"]
