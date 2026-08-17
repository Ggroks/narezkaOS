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


# --- настройка оформления ---------------------------------------------------


def test_word_limit_breaks_the_line(): 
    """Предел по словам задают на глаз: «не больше трёх в строке»."""
    from narezka.core.subtitles import preset_style, wrap_words

    words = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25}
             for i, w in enumerate("раз два три четыре пять шесть".split())]
    lines = wrap_words(words, max_chars=100, max_words=2)
    assert [len(line) for line in lines] == [2, 2, 2]

    style = preset_style("classic", {"max_words_per_line": 1})
    assert style.max_words_per_line == 1


def test_long_word_still_wraps_by_width():
    """Предел по символам никуда не девается: «Здравствуйте, уважаемые» —
    это два слова, но полторы строки."""
    from narezka.core.subtitles import wrap_words

    words = [{"word": "Здравствуйте,", "start": 0, "end": 0.5},
             {"word": "уважаемые", "start": 0.5, "end": 1.0}]
    lines = wrap_words(words, max_chars=14, max_words=5)
    assert len(lines) == 2


def test_position_changes_alignment():
    """ASS считает выравнивание цифрами; наружу отдаются слова."""
    from narezka.core.subtitles import build_ass, preset_style

    words = [{"word": "тест", "start": 0.0, "end": 0.5}]
    for position, alignment in (("bottom", "2"), ("middle", "5"), ("top", "8")):
        style = preset_style("classic", {"position": position})
        ass = build_ass(words, style=style, width=1080, height=1920)
        line = next(l for l in ass.splitlines() if l.startswith("Style:"))
        assert line.split(",")[18] == alignment, position


def test_colour_conversion_is_reversible():
    """Порядок байтов в ASS обратный привычному, и перепутать его легко:
    ошибка выглядит как «сделал жёлтый, получил синий»."""
    from narezka.core.subtitles import ass_colour, hex_colour

    assert ass_colour("#FFCC00") == "&H0000CCFF"
    assert hex_colour("&H0000CCFF") == "#FFCC00"
    for colour in ("#FFFFFF", "#000000", "#33D6FF", "#FF3B30"):
        assert hex_colour(ass_colour(colour)) == colour


def test_bad_colour_is_refused():
    from narezka.core.subtitles import ass_colour

    with pytest.raises(ValueError):
        ass_colour("почти жёлтый")


def test_preset_keeps_untouched_fields():
    """Человек меняет цвет и ждёт, что остальное останется от набора."""
    from narezka.core.subtitles import PRESETS, preset_style

    base = preset_style("loud")
    tuned = preset_style("loud", {"primary": "&H0000FFFF"})
    assert tuned.font_size == base.font_size and tuned.outline == base.outline
    assert tuned.primary != base.primary
    assert set(PRESETS) >= {"classic", "loud", "calm", "center", "one_word"}


def test_old_style_names_still_work():
    """Записи, сделанные до пресетов, ссылаются на STYLE_1: молча подставить
    другой стиль значило бы поменять готовые ролики."""
    from narezka.core.subtitles import preset_style

    assert preset_style("STYLE_1").name == "classic"
    assert preset_style("STYLE_3").font == "DejaVu Serif"


def test_colour_must_be_hexadecimal():
    """Найдено тестом: «жёлтый» — ровно шесть знаков, и проверка длины
    пропускала его, превращая в цвет «&H00ЫЙЛТЖЁ». Увидеть это можно было бы
    только на готовом ролике."""
    from narezka.core.subtitles import ass_colour

    for bad in ("жёлтый", "ffcc0g", "#12345", "не цвет"):
        with pytest.raises(ValueError):
            ass_colour(bad)
