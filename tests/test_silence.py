"""Вырезка пауз.

Проверяется не только то, что паузы находятся, но и то, что нужные паузы
**не** трогаются: речь без пауз вовсе звучит тараторящей, а вырезанная
драматическая пауза убивает шутку.
"""

import numpy as np
import pytest

from narezka.core.silence import find_pauses, plan_cuts


def track(pattern: list[tuple[float, int]]) -> np.ndarray:
    """Дорожка громкости: пары (уровень в дБ, сколько окон)."""
    return np.concatenate([np.full(count, level) for level, count in pattern])


def test_pause_is_found_relative_to_median():
    """Порог считается от медианы записи, а не в абсолютных децибелах.

    У каждой записи свой уровень: общий порог на тихой вырезал бы речь,
    а на громкой не находил бы пауз.
    """
    rms = track([(-20.0, 10), (-60.0, 5), (-20.0, 10)])
    pauses = find_pauses(rms, 1.0)
    assert pauses == [(10.0, 15.0)]


def test_short_pause_is_left_alone():
    """На коротких паузах держится ритм речи — их не трогают."""
    rms = track([(-20.0, 10), (-60.0, 1), (-20.0, 10)])
    assert find_pauses(rms, 0.1) == []


def test_part_of_the_pause_stays():
    """Из паузы вырезается не вся длина: речь встык звучит неестественно."""
    rms = track([(-20.0, 10), (-60.0, 20), (-20.0, 10)])
    report = plan_cuts(rms, 0.1, 4.0)

    assert report.cut == 1
    # Пауза 2 с: отступы по краям и оставленная часть делают вырезку короче.
    assert 0 < report.removed_seconds < 2.0


def test_dramatic_pause_before_speech_is_kept():
    """Молчание перед репликой — часть реплики, его не режут."""
    rms = track([(-20.0, 10), (-60.0, 10), (-20.0, 10)])
    words = [{"start": 2.05, "end": 2.5, "word": "и"}]

    report = plan_cuts(rms, 0.1, 3.0, words)

    assert report.found == 1, "пауза найдена"
    assert report.cut == 0, "но не вырезана — она драматическая"
    assert report.edl.is_identity


def test_long_silence_is_cut_even_before_speech():
    """Очень долгое молчание вырезается: это уже не пауза, а простой."""
    rms = track([(-20.0, 5), (-60.0, 200), (-20.0, 5)])
    words = [{"start": 20.6, "end": 21.0, "word": "итак"}]

    report = plan_cuts(rms, 0.1, 21.0, words)

    assert report.cut == 1
    assert report.removed_seconds > 15.0


def test_cut_keeps_margin_from_words():
    """Рез не подходит вплотную к слову — иначе оно звучит обрубленным."""
    rms = track([(-20.0, 10), (-60.0, 30), (-20.0, 10)])
    report = plan_cuts(rms, 0.1, 5.0, [], word_margin=0.3, keep_pause=0.0)

    span = report.edl.spans
    assert span[0].end == pytest.approx(1.3), "отступ после последнего слова"


def test_no_pauses_gives_identity():
    """Нет пауз — ось времени тождественна, а не пустая."""
    report = plan_cuts(track([(-20.0, 30)]), 0.1, 3.0)
    assert report.edl.is_identity
    assert report.removed_seconds == 0.0


def test_silence_only_recording_is_safe():
    """Запись из одной тишины не должна ронять расчёт."""
    report = plan_cuts(track([(-60.0, 30)]), 0.1, 3.0)
    assert report.found in (0, 1)


def test_empty_track():
    report = plan_cuts(np.zeros(0), 0.1, 0.0)
    assert report.edl.is_identity


def test_timeline_stays_consistent_after_cuts():
    """Свойство: после вырезки каждый сохранённый момент обратим.

    Это связка с осью времени: сама вырезка бесполезна, если субтитры
    после неё нельзя пересчитать обратно.
    """
    rms = track([(-20.0, 20), (-60.0, 30), (-20.0, 20), (-60.0, 25), (-20.0, 20)])
    report = plan_cuts(rms, 0.1, 11.5)

    for span in report.edl.spans:
        middle = (span.start + span.end) / 2
        out = report.edl.to_output(middle)
        assert out is not None
        assert report.edl.to_source(out) == pytest.approx(middle, abs=1e-4)
