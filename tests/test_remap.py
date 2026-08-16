"""Пересчёт транскрипта и клипов в выходное время.

Обязательная пара к вырезке пауз: включить вырезку без пересчёта значит
гарантированно рассинхронить субтитры.
"""

import pytest

from narezka.core.edl import Edl
from narezka.core.remap import remap_clips, remap_segments, remap_words, verify


def words(*items):
    return [{"word": w, "start": s, "end": e} for w, s, e in items]


def test_identity_leaves_everything():
    edl = Edl.identity()
    original = words(("привет", 1.0, 1.5))
    assert remap_words(original, edl) == original


def test_words_after_a_cut_shift_back():
    """Слово после вырезки сдвигается ровно на её длину."""
    edl = Edl.cut(100.0, [(10.0, 20.0)])
    moved = remap_words(words(("да", 30.0, 30.5)), edl)

    assert moved[0]["start"] == pytest.approx(20.0)
    assert moved[0]["end"] == pytest.approx(20.5)


def test_source_time_is_kept():
    """Исходное время сохраняется: по нему ищут кадр и сверяют расшифровку.

    Восстановить его вычитанием нельзя — вырезок может быть много.
    """
    edl = Edl.cut(100.0, [(10.0, 20.0)])
    moved = remap_words(words(("да", 30.0, 30.5)), edl)
    assert moved[0]["source_start"] == pytest.approx(30.0)


def test_word_inside_a_cut_disappears():
    edl = Edl.cut(100.0, [(10.0, 20.0)])
    assert remap_words(words(("э", 14.0, 15.0)), edl) == []


def test_word_clipped_by_an_edge_narrows():
    """Задетое краем слово сужается, а не выбрасывается.

    Его звук частично остался, и выбросить слово значит потерять подсветку
    там, где зритель слышит речь.
    """
    edl = Edl.cut(100.0, [(10.0, 20.0)])
    moved = remap_words(words(("длинное", 8.0, 12.0)), edl)

    assert len(moved) == 1
    assert moved[0]["end"] == pytest.approx(10.0)


def test_segment_bounds_follow_surviving_words():
    """Границы строки берутся по уцелевшим словам, а не пересчитываются сами.

    Иначе строка субтитра начиналась бы раньше первого слова, которое в ней
    осталось.
    """
    edl = Edl.cut(100.0, [(10.0, 20.0)])
    segments = [{
        "start": 5.0, "end": 30.0,
        "words": words(("раз", 5.0, 6.0), ("два", 14.0, 15.0), ("три", 25.0, 26.0)),
    }]

    moved = remap_segments(segments, edl)

    assert len(moved[0]["words"]) == 2, "слово в вырезанном пропало"
    assert moved[0]["start"] == pytest.approx(5.0)
    assert moved[0]["end"] == pytest.approx(16.0), "конец по последнему уцелевшему слову"


def test_segment_without_words_still_moves():
    edl = Edl.cut(100.0, [(10.0, 20.0)])
    moved = remap_segments([{"start": 30.0, "end": 40.0}], edl)
    assert moved[0]["start"] == pytest.approx(20.0)


def test_segment_fully_cut_disappears():
    edl = Edl.cut(100.0, [(10.0, 20.0)])
    assert remap_segments([{"start": 12.0, "end": 18.0}], edl) == []


def test_clip_duration_is_recomputed():
    """Длительность клипа пересчитывается, а не переносится старая."""
    edl = Edl.cut(100.0, [(30.0, 40.0)])
    moved = remap_clips([{"start": 25.0, "end": 50.0, "duration": 25.0}], edl)

    assert moved[0]["duration"] == pytest.approx(moved[0]["end"] - moved[0]["start"])
    assert moved[0]["duration"] < 25.0, "часть клипа вырезана"


def test_clip_peak_inside_a_cut_falls_back_to_start():
    """Пик мог попасть в вырезанное — клип остаётся, точка отсчёта нужна."""
    edl = Edl.cut(100.0, [(30.0, 40.0)])
    moved = remap_clips([{"start": 25.0, "end": 50.0, "peak_at": 35.0}], edl)

    assert moved[0]["peak_at"] == pytest.approx(moved[0]["start"])


def test_clip_fully_cut_disappears():
    edl = Edl.cut(100.0, [(30.0, 40.0)])
    assert remap_clips([{"start": 32.0, "end": 38.0}], edl) == []


def test_verify_passes_on_correct_remap():
    """Проверка молчит, когда всё сходится."""
    edl = Edl.cut(100.0, [(10.0, 20.0), (50.0, 55.0)])
    moved = remap_words(words(("раз", 5.0, 6.0), ("два", 30.0, 31.0), ("три", 60.0, 61.0)), edl)
    assert verify(moved, edl) == []


def test_verify_catches_broken_order():
    """Проверка ловит перекрытие — признак рассинхрона."""
    edl = Edl.identity()
    broken = [
        {"word": "раз", "start": 5.0, "end": 9.0},
        {"word": "два", "start": 1.0, "end": 2.0},
    ]
    assert any("перекрывает" in p for p in verify(broken, edl))


def test_verify_catches_wrong_source_link():
    """Проверка ловит расхождение с сохранённым исходным временем."""
    edl = Edl.cut(100.0, [(10.0, 20.0)])
    wrong = [{"word": "да", "start": 20.0, "end": 20.5, "source_start": 99.0}]
    assert any("обратный пересчёт" in p for p in verify(wrong, edl))
