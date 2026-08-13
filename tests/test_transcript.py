"""Тесты отсева галлюцинаций распознавания (BAZA.md §57).

Случаи взяты из того, что Whisper реально выдаёт на музыке, игровом звуке
и тишине: титры несуществующих переводчиков, зацикленные фразы, вежливые
концовки роликов.
"""

from __future__ import annotations

from typing import Any

import pytest

from narezka.core.transcript import (
    check_segment,
    find_repeat,
    is_loop,
    mark_suspect_segments,
    normalize,
    usable_segments,
)

THRESHOLDS = {"max_no_speech_prob": 0.6, "min_avg_logprob": -1.0, "max_repeat_ratio": 0.5}


def segment(text: str, **overrides: Any) -> dict[str, Any]:
    """Сегмент с уверенными показателями — чтобы проверялся именно текст."""
    base = {"text": text, "no_speech_prob": 0.05, "avg_logprob": -0.3, "words": []}
    base.update(overrides)
    return base


# --- нормализация ----------------------------------------------------------


def test_normalize_strips_punctuation_and_case() -> None:
    assert normalize("Это — Невероятный МОМЕНТ!!!") == "это невероятный момент"


def test_normalize_handles_empty() -> None:
    assert normalize("...!!!") == ""


# --- обнаружение зацикливания ---------------------------------------------


@pytest.mark.parametrize(
    ("words", "expected_at_least"),
    [
        (["да", "да", "да", "да"], 4),
        (["спасибо", "за", "просмотр", "спасибо", "за", "просмотр", "спасибо", "за", "просмотр"], 3),
        (["привет", "как", "дела", "хорошо"], 1),
    ],
)
def test_find_repeat_counts_repeats(words: list[str], expected_at_least: int) -> None:
    assert find_repeat(words).repeats >= expected_at_least


def test_no_false_repeat_on_normal_speech() -> None:
    words = "сейчас я попробую убить этого босса и посмотрим что получится".split()
    assert find_repeat(words).repeats < 3


def test_loop_detected_when_repeat_dominates_segment() -> None:
    assert is_loop("спасибо спасибо спасибо спасибо".split())


def test_many_repeats_are_a_loop_regardless_of_length() -> None:
    words = ("да " * 6 + "поехали дальше по карте смотрим что там").split()
    assert is_loop(words)


def test_embedded_repetition_is_not_a_loop() -> None:
    """Реальный случай из фикстуры: звукоподражание внутри обычной фразы.

    До появления фикстур фильтр помечал это галлюцинацией. В стриме
    эмоциональный повтор — признак интересного момента, а не выдумки модели.
    """
    words = normalize("его и жиг, жиг, жиг, готово. Почти лысый, домашний").split()
    assert not is_loop(words)


# --- типовые галлюцинации --------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Субтитры сделал DimaTorzok",
        "субтитры создавал dimatorzok",
        "Продолжение следует...",
        "Спасибо за просмотр!",
        "Подписывайтесь на канал",
        "Thanks for watching!",
        "Subscribe to my channel",
    ],
)
def test_known_hallucinations_are_caught(text: str) -> None:
    verdict = check_segment(segment(text), **THRESHOLDS)
    assert verdict.suspect, f"не поймано: {text}"
    assert "галлюцинация" in verdict.reason


@pytest.mark.parametrize("marker", ["♪", "♫", "[музыка]", "[Music]"])
def test_music_markers_are_caught(marker: str) -> None:
    assert check_segment(segment(f"{marker} {marker}"), **THRESHOLDS).suspect


def test_empty_text_is_suspect() -> None:
    assert check_segment(segment("   "), **THRESHOLDS).suspect


# --- метрики модели --------------------------------------------------------


def test_high_no_speech_probability_is_caught() -> None:
    verdict = check_segment(segment("какой-то текст", no_speech_prob=0.92), **THRESHOLDS)
    assert verdict.suspect
    assert "отсутствия речи" in verdict.reason


def test_low_confidence_is_caught() -> None:
    verdict = check_segment(segment("какой-то текст", avg_logprob=-2.4), **THRESHOLDS)
    assert verdict.suspect
    assert "уверенность" in verdict.reason


def test_missing_metrics_do_not_crash() -> None:
    """У сегмента может не быть метрик — это не повод его отбрасывать."""
    verdict = check_segment({"text": "нормальная фраза про игру", "words": []}, **THRESHOLDS)
    assert not verdict.suspect


# --- зацикливание внутри сегмента -----------------------------------------


def test_looped_phrase_is_caught() -> None:
    verdict = check_segment(segment("Спасибо. Спасибо. Спасибо. Спасибо."), **THRESHOLDS)
    assert verdict.suspect


def test_low_vocabulary_is_caught() -> None:
    verdict = check_segment(segment("да да да нет да да нет да да да"), **THRESHOLDS)
    assert verdict.suspect


# --- нормальная речь не должна отбраковываться -----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Ребята, сейчас я попробую убить этого босса",
        "Да ладно, ты серьёзно это сделал?",
        "Не делай этого, у тебя мало хп осталось",
        "Окей, погнали дальше по карте",
        "Вот это был невероятный момент, я в шоке",
    ],
)
def test_normal_speech_passes(text: str) -> None:
    verdict = check_segment(segment(text), **THRESHOLDS)
    assert not verdict.suspect, f"ложное срабатывание на: {text} ({verdict.reason})"


def test_short_repetition_in_real_speech_passes() -> None:
    """Повтор слова — обычное явление живой речи, это не галлюцинация."""
    verdict = check_segment(segment("нет нет подожди сейчас будет самое интересное"), **THRESHOLDS)
    assert not verdict.suspect


# --- межсегментное зацикливание -------------------------------------------


def test_repeated_text_across_segments_is_caught() -> None:
    segments = [
        segment("Продолжаем игру"),
        segment("Спасибо за внимание"),
        segment("Спасибо за внимание"),
        segment("Спасибо за внимание"),
    ]
    mark_suspect_segments(segments, **THRESHOLDS)
    assert not segments[0]["suspect"]
    assert all(s["suspect"] for s in segments[1:])


def test_marking_is_idempotent() -> None:
    segments = [segment("нормальная реплика"), segment("Спасибо за просмотр")]
    mark_suspect_segments(segments, **THRESHOLDS)
    first = [s["suspect"] for s in segments]
    mark_suspect_segments(segments, **THRESHOLDS)
    assert [s["suspect"] for s in segments] == first


def test_clean_segment_has_no_stale_reason() -> None:
    """Повторная разметка не должна оставлять причину на очищенном сегменте."""
    segments = [segment("Спасибо за просмотр")]
    mark_suspect_segments(segments, **THRESHOLDS)
    assert "suspect_reason" in segments[0]

    segments[0]["text"] = "Ребята, смотрите что сейчас будет"
    mark_suspect_segments(segments, **THRESHOLDS)
    assert not segments[0]["suspect"]
    assert "suspect_reason" not in segments[0]


def test_usable_segments_filters_suspect() -> None:
    segments = [segment("нормальная реплика"), segment("Спасибо за просмотр")]
    mark_suspect_segments(segments, **THRESHOLDS)
    usable = usable_segments(segments)
    assert len(usable) == 1
    assert usable[0]["text"] == "нормальная реплика"


def test_segments_are_marked_not_deleted() -> None:
    """§57: помечать, а не удалять — иначе отладка становится невозможной."""
    segments = [segment("Спасибо за просмотр"), segment("нормальная реплика")]
    result = mark_suspect_segments(segments, **THRESHOLDS)
    assert len(result) == 2, "фильтр не должен удалять сегменты"
