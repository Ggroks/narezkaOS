"""Склейка отрезков в длинный ролик.

Первая версия строила один вызов ffmpeg с `trim`+`concat`, без файлов на
диске. На сюжетном режиме это работало, а на подборке из 28 кусков вразнобой
съело всю память машины: `concat` требует куски по порядку, входной файл
читается по порядку, и раскодированное копится, пока склейка до него не дойдёт.
"""

from pathlib import Path

import pytest

from narezka.core.assemble import concat_list, total_duration


def test_duration_sums_the_pieces():
    assert total_duration([(10, 70), (200, 260)]) == pytest.approx(120.0)


def test_negative_piece_does_not_subtract():
    """Испорченный отрезок не должен уменьшать общую длительность."""
    assert total_duration([(10, 70), (100, 50)]) == pytest.approx(60.0)


def test_empty_input_is_zero():
    assert total_duration([]) == 0.0


def test_listing_keeps_order():
    """Порядок кусков в описи — это порядок в ролике.

    В подборке он не хронологический: зацепка снята в середине записи,
    а стоит первой.
    """
    listing = concat_list([Path("/x/002.mp4"), Path("/x/000.mp4")])
    assert listing.index("002.mp4") < listing.index("000.mp4")


def test_listing_escapes_apostrophes():
    """Путь с апострофом иначе разорвёт строку описи, и ffmpeg прочтёт мусор."""
    listing = concat_list([Path("/tmp/it's/a.mp4")])
    assert r"'\''" in listing
    assert listing.count("file '") == 1


def test_listing_ends_with_a_newline():
    """Без завершающего перевода строки ffmpeg теряет последний файл."""
    assert concat_list([Path("/a.mp4")]).endswith("\n")


def test_listing_uses_absolute_paths():
    """Опись читается ffmpeg из своего каталога — относительный путь не найдётся."""
    listing = concat_list([Path("a.mp4")])
    assert listing.startswith("file '/")
