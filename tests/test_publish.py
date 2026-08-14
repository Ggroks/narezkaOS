"""Тесты текстов для публикации (BAZA.md §22, §23, §24).

§22 требует, чтобы вариант заголовка выбирала **система**, а не модель.
Проверить соответствие заголовка содержанию автоматически нельзя, но отсечь
типовые приёмы завлечения — можно, и решение должно быть воспроизводимым:
видно, какой вариант отвергнут и почему.
"""

from __future__ import annotations

import pytest

from narezka.core.publish import (
    MAX_TITLE_CHARS,
    build_entry,
    choose_title,
    clean_hashtags,
    is_clickbait,
    render_description,
    title_problems,
)


# --- кликбейт --------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Ты не поверишь что случилось дальше",
        "ШОК: стример сделал это",
        "Никто не ожидал такого финала",
        "You won't believe this",
        "ОГРОМНЫМИ БУКВАМИ КРИЧУ",
    ],
)
def test_clickbait_is_detected(title: str) -> None:
    assert is_clickbait(title)


@pytest.mark.parametrize(
    "title",
    [
        "Отец гордится сыном: момент признания",
        "Как продать жвачку с юмором",
        "СЕО объясняет решение",
    ],
)
def test_normal_titles_pass(title: str) -> None:
    assert not is_clickbait(title), f"ложное срабатывание: {title}"


def test_short_abbreviation_is_not_caps_shouting() -> None:
    """Аббревиатура из четырёх букв — не крик капслоком."""
    assert not is_clickbait("Разбор HTML и CSS за минуту")


# --- отбор заголовка -------------------------------------------------------


def test_first_clean_variant_wins() -> None:
    """Порядок задаёт модель — это её предпочтение; система лишь отбраковывает."""
    title, reviewed = choose_title(["ШОК контент", "Спокойный заголовок", "Третий"])
    assert title == "Спокойный заголовок"
    assert reviewed[0]["problems"] == ["похоже на кликбейт"]
    assert reviewed[1]["problems"] == []


def test_too_long_title_is_flagged() -> None:
    problems = title_problems("а" * (MAX_TITLE_CHARS + 5))
    assert problems == [f"длиннее {MAX_TITLE_CHARS} символов"]


def test_long_title_is_trimmed_when_nothing_else_fits() -> None:
    """Слишком длинный можно обрезать, а кликбейт лучше не публиковать вовсе."""
    title, _ = choose_title(["ШОК всех времён", "б" * (MAX_TITLE_CHARS + 20)])
    assert len(title) <= MAX_TITLE_CHARS
    assert title.endswith("…")


def test_only_clickbait_falls_back_instead_of_publishing_it() -> None:
    title, _ = choose_title(["ШОК контент", "Ты не поверишь"], fallback="Момент из стрима")
    assert title == "Момент из стрима"


def test_empty_variants_use_the_fallback() -> None:
    assert choose_title([], fallback="Запасной")[0] == "Запасной"


def test_review_records_every_variant() -> None:
    """Без разбора непонятно, почему выбран именно этот заголовок."""
    _, reviewed = choose_title(["ШОК", "Нормальный", "Тоже нормальный"])
    assert len(reviewed) == 3


# --- хэштеги ---------------------------------------------------------------


def test_hash_and_junk_are_stripped() -> None:
    assert clean_hashtags(["#стрим", "  юмор  ", "#не!!где"]) == ["#стрим", "#юмор", "#негде"]


def test_engagement_bait_is_dropped() -> None:
    """§24: бессмысленные хэштеги не добавлять."""
    assert clean_hashtags(["рекомендации", "fyp", "подпишись", "стрим"]) == ["#стрим"]


def test_duplicates_are_removed_case_insensitively() -> None:
    assert clean_hashtags(["#Стрим", "стрим", "СТРИМ"]) == ["#Стрим"]


def test_numbers_and_single_chars_are_rejected() -> None:
    assert clean_hashtags(["123", "а", "нормальный"]) == ["#нормальный"]


def test_count_is_capped() -> None:
    assert len(clean_hashtags([f"тег{i}" for i in range(30)])) == 8


def test_order_is_preserved() -> None:
    """Первыми модель ставит более осмысленные — порядок несёт информацию."""
    assert clean_hashtags(["первый", "второй", "третий"]) == ["#первый", "#второй", "#третий"]


# --- сборка записи ---------------------------------------------------------


def test_entry_is_assembled_from_the_answer() -> None:
    clip = {"index": 2, "clip_id": "v_c02", "start": 10.0, "end": 40.0, "explanation": "смешно"}
    entry = build_entry(
        clip,
        {
            "titles": ["ШОК заголовок", "Нормальный заголовок"],
            "description": "  Описание момента.  ",
            "hashtags": ["стрим", "fyp", "юмор"],
        },
    )
    assert entry["title"] == "Нормальный заголовок"
    assert entry["description"] == "Описание момента."
    assert entry["hashtags"] == ["#стрим", "#юмор"]
    assert entry["index"] == 2


def test_description_and_hashtags_render_together() -> None:
    entry = {"description": "Что-то произошло.", "hashtags": ["#стрим", "#юмор"]}
    assert render_description(entry) == "Что-то произошло.\n\n#стрим #юмор"


def test_description_without_hashtags_has_no_trailing_blank() -> None:
    assert render_description({"description": "Только текст.", "hashtags": []}) == "Только текст."
