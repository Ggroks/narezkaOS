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


def test_emphatic_repetition_inside_a_sentence_is_speech() -> None:
    """Настоящие случаи с записи стрима, отменившие прежнее правило.

    Раньше пять повторов подряд считались петлёй безусловно. На живой записи
    так отбраковались «круто круто круто круто круто круто круто… прикинь»
    и «да, да, да… там навалилось всё вместе» — семь и девять повторов внутри
    связной фразы. Это возбуждённая речь, то есть ровно те моменты, ради
    которых строится отбор (§13). У петли модели связного текста вокруг нет.
    """
    for text in (
        "йо тут еще есть физика лаза не как в пике прикинь круто круто круто круто круто круто круто не",
        "на этом разошли собственно говоря да да да да да да да да да там навалилось всё вместе",
        "да да да да да да поехали дальше по карте смотрим что там",
    ):
        assert not is_loop(text.split()), f"эмоциональный повтор принят за петлю: {text}"


def test_very_short_segment_is_not_judged() -> None:
    """«Ладно, ладно, ладно» — три слова, все повторы, и при этом обычная речь.
    На таком объёме доля перестаёт что-либо означать."""
    assert not is_loop("ладно ладно ладно".split())


@pytest.mark.parametrize(
    "text",
    [
        "Ха-ха-ха-ха-ха!",
        "А-а-а!",
        "А? А-а-а!",
        "Хе-хе-хе",
        "Ого-го-го!",
    ],
)
def test_laughter_and_screams_are_not_hallucinations(text: str) -> None:
    """Настоящие случаи из видеофикстуры.

    Смех и крик — ровно то, что ищет отбор моментов (§13). Первая версия
    фильтра рвала «ха-ха-ха» по дефисам и объявляла зацикливанием, то есть
    вырезала лучшее. Звукоподражание — одно слово, а не повтор фразы.
    """
    verdict = check_segment(segment(text), **THRESHOLDS)
    assert not verdict.suspect, f"вырезано как галлюцинация: {text} ({verdict.reason})"


def test_hyphenated_word_stays_one_token() -> None:
    assert normalize("Ха-ха-ха!") == "ха-ха-ха"
    assert normalize("что-то, где-то") == "что-то где-то"


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


# --- шум на фоне против настоящего молчания --------------------------------


def test_loud_background_does_not_condemn_clear_speech() -> None:
    """Замер на записи стрима: 19 сегментов живой речи были отброшены
    по no_speech_prob 0.64–0.85, при том что модель расслышала их отлично
    (avg_logprob −0.15…−0.31 при медиане по записи −0.21).

    На стриме фоном идёт звук игры, и no_speech_prob подскакивает от него,
    а не от отсутствия речи. На студийной дорожке мультфильма его медиана
    0.007, на стриме 0.051 — в семь раз выше.
    """
    verdict = check_segment(
        segment("Блять, а где другого компа, провод?", no_speech_prob=0.76, avg_logprob=-0.2),
        **THRESHOLDS,
    )
    assert not verdict.suspect, f"живая речь отброшена: {verdict.reason}"


def test_near_certain_silence_is_caught_anyway() -> None:
    """Выше 0.9 молчание практически достоверно — уверенность не спасает."""
    verdict = check_segment(
        segment("какой-то текст", no_speech_prob=0.95, avg_logprob=-0.2), **THRESHOLDS
    )
    assert verdict.suspect
    assert "отсутствия речи" in verdict.reason


def test_high_no_speech_with_unsure_model_is_still_caught() -> None:
    """Когда модель и сама не уверена в тексте, подтверждений достаточно."""
    verdict = check_segment(
        segment("бу бу бу", no_speech_prob=0.7, avg_logprob=-0.9), **THRESHOLDS
    )
    assert verdict.suspect


def test_known_hallucination_is_caught_regardless_of_confidence() -> None:
    """Смягчение касается только no_speech_prob: титры остаются титрами,
    как бы уверенно модель их ни выдумала."""
    verdict = check_segment(
        segment("Субтитры сделал DimaTorzok", no_speech_prob=0.05, avg_logprob=-0.1),
        **THRESHOLDS,
    )
    assert verdict.suspect


def test_loop_is_caught_regardless_of_confidence() -> None:
    verdict = check_segment(
        segment("Спасибо. Спасибо. Спасибо. Спасибо.", no_speech_prob=0.05, avg_logprob=-0.1),
        **THRESHOLDS,
    )
    assert verdict.suspect
