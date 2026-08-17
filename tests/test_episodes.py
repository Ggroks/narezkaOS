"""Поиск эпизодов: связных занятий с началом и концом."""

import json

import pytest

from narezka.core.episodes import Episode, chunks, merge, parse, transcript_digest


def test_short_recording_is_one_window():
    assert chunks(1800.0) == [(0.0, 1800.0)]


def test_windows_overlap():
    """Эпизод, начавшийся у края окна, должен целиком попасть в следующее."""
    windows = chunks(10000.0, size=3600.0, overlap=600.0)
    for (_, end), (start, _) in zip(windows, windows[1:], strict=False):
        assert start < end, "окна должны перекрываться"


def test_tail_is_absorbed_not_left_as_a_stub():
    """Огрызок в конце прирастает к предыдущему окну.

    Пять минут обрывка не дадут модели ничего, кроме шанса выдумать
    эпизод из ничего.
    """
    windows = chunks(3900.0, size=3600.0, overlap=600.0)
    assert all(end - start >= 1800.0 for start, end in windows)
    assert windows[-1][1] == pytest.approx(3900.0)


def test_digest_keeps_the_flow_not_every_word():
    """Сжатая расшифровка: строка на полминуты, с таймкодами."""
    segments = [{"start": i * 10.0, "text": f"фраза {i}"} for i in range(9)]
    digest = transcript_digest(segments, 0.0, 90.0, step=30.0)

    assert digest.count("\n") == 2, "три строки на полторы минуты"
    assert "[0:00]" in digest and "[1:00]" in digest


def test_digest_ignores_segments_outside_the_window():
    segments = [{"start": 5.0, "text": "внутри"}, {"start": 500.0, "text": "снаружи"}]
    assert "снаружи" not in transcript_digest(segments, 0.0, 100.0)


def test_parse_reads_a_list():
    answer = json.dumps([
        {"start": 100, "end": 900, "title": "раунд на Мираже",
         "summary": "от закупки до победы", "coherence": 0.8}
    ])
    episodes = parse(answer, 0.0, 3600.0)

    assert len(episodes) == 1
    assert episodes[0].title == "раунд на Мираже"
    assert episodes[0].duration == pytest.approx(800.0)


def test_parse_survives_surrounding_chatter():
    """Модель любит обрамлять ответ словами — это не повод терять разметку."""
    answer = 'Вот что я нашёл:\n[{"start":0,"end":600,"title":"т","coherence":0.5}]\nГотово.'
    assert len(parse(answer, 0.0, 3600.0)) == 1


def test_parse_drops_bad_entries_but_keeps_good_ones():
    """Мусорный пункт не должен ронять разметку всего окна."""
    answer = json.dumps([
        {"start": 0, "end": 60, "title": "слишком короткий"},
        {"start": 100, "end": 900, "title": "годный"},
        {"start": 100, "end": 900},
    ])
    episodes = parse(answer, 0.0, 3600.0)

    assert [e.title for e in episodes] == ["годный"]


def test_parse_clamps_to_the_window():
    """Модель иногда выходит за окно, пересказывая то, чего в нём не было."""
    answer = json.dumps([{"start": -500, "end": 9999, "title": "т"}])
    episodes = parse(answer, 0.0, 1000.0)

    assert episodes[0].start == 0.0 and episodes[0].end == 1000.0


def test_parse_handles_garbage():
    assert parse("модель отказалась отвечать", 0.0, 3600.0) == []


def test_merge_removes_duplicates_from_window_seams():
    """Один и тот же эпизод, найденный в двух окнах, остаётся одним.

    Показывать его дважды значит врать о числе вариантов.
    """
    a = Episode(100, 900, "раунд", "", 0.6)
    b = Episode(120, 880, "тот же раунд", "", 0.9)

    merged = merge([a, b])

    assert len(merged) == 1
    assert merged[0].coherence == 0.9, "остаётся более цельный вариант"


def test_merge_keeps_separate_episodes():
    a = Episode(0, 600, "первый", "", 0.7)
    b = Episode(1000, 1800, "второй", "", 0.7)
    assert len(merge([a, b])) == 2
