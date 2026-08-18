"""Тесты шрифтов субтитров (BAZA.md §60, §62).

Смысл проверок: результат рендера не должен зависеть от того, какие шрифты
установлены на машине. Подстановка «чем-нибудь похожим» на кириллице даёт
пустые прямоугольники, и заметно это только на готовом ролике.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from narezka.core import fonts
from narezka.core.framing import Framing, build_filter, plan_frame
from narezka.core.subtitles import STYLES, build_ass


def test_declared_fonts_are_present() -> None:
    """Всё, что объявлено в поставке, лежит в репозитории."""
    assert fonts.missing_files() == [], "шрифт объявлен, но не вендорён"


def test_fonts_dir_points_inside_repository() -> None:
    """Каталог ищется относительно кода, а не текущей директории.

    Иначе стадия работает только при запуске из корня проекта.
    """
    assert fonts.fonts_dir().is_dir()
    assert fonts.fonts_dir().name == "fonts"


def test_license_ships_with_the_fonts() -> None:
    """Лицензия Bitstream Vera требует прикладывать текст к каждой копии."""
    license_file = fonts.fonts_dir() / "LICENSE.txt"
    assert license_file.is_file()
    assert "Bitstream" in license_file.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("style", STYLES.values(), ids=list(STYLES))
def test_every_style_uses_a_vendored_family(style) -> None:
    """Ни один пресет не должен зависеть от шрифта с машины."""
    assert fonts.is_vendored(style.font), f"{style.name}: «{style.font}» не в поставке"


def test_unknown_family_falls_back_instead_of_breaking() -> None:
    assert fonts.resolve_family("Comic Sans MS") == fonts.FALLBACK_FAMILY
    assert fonts.is_vendored(fonts.FALLBACK_FAMILY)


def test_ass_names_a_vendored_family() -> None:
    """В самом файле субтитров должно стоять имя из поставки."""
    words = [{"word": "привет", "start": 0.0, "end": 0.4, "probability": 0.9}]
    ass = build_ass(words, style=STYLES["STYLE_1"], width=1080, height=1920)
    # Проверяется суть, а не конкретное имя: в стиле обязано стоять
    # семейство из поставки. Какое именно — решает набор оформления,
    # и оно менялось, когда в поставку добавили популярные шрифты.
    named = next(line for line in ass.splitlines() if line.startswith("Style:")).split(",")[1]
    assert fonts.is_vendored(named), named


# --- путь внутри строки фильтра -------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/home/user/fonts", "/home/user/fonts"),
        # Двоеточие разделяет параметры фильтра — в путях Windows оно обычное.
        (r"C:\fonts", r"C\:\\fonts"),
        ("/tmp/it's", r"/tmp/it\'s"),
    ],
)
def test_filter_path_is_escaped(raw: str, expected: str) -> None:
    assert fonts.escape_for_filter(Path(raw)) == expected


def test_filter_passes_the_fonts_directory() -> None:
    framing = Framing()
    chain = build_filter(
        plan_frame(1024, 576, 1080, 1920, framing),
        framing,
        1080,
        1920,
        "00.ass",
        fonts_dir="/opt/fonts",
    )
    assert "subtitles=00.ass:fontsdir=/opt/fonts" in chain


def test_preview_chain_needs_no_fonts() -> None:
    """Без субтитров каталог шрифтов в цепочке лишний."""
    framing = Framing()
    chain = build_filter(
        plan_frame(1024, 576, 1080, 1920, framing), framing, 1080, 1920,
        fonts_dir="/opt/fonts",
    )
    assert "fontsdir" not in chain


# --- популярные шрифты в поставке -------------------------------------------


def test_popular_families_are_vendored():
    """Шрифты лежат в репозитории, а не берутся с машины рендера (§60):
    иначе на другом компьютере ролик выйдет другим."""
    from narezka.core import fonts

    for family in ("Montserrat", "Oswald", "Inter", "Roboto", "Open Sans", "Golos Text"):
        assert fonts.is_vendored(family), family
        assert fonts.FONT_NOTES.get(family), f"нет пояснения для {family}"


def test_every_vendored_family_covers_russian():
    """Главная проверка, а не «шрифт популярный»: читается таблица символов
    самого файла. Подстановка вместо буквы даёт пустой прямоугольник,
    и заметно это только на готовом ролике."""
    import struct

    from narezka.core import fonts

    def codepoints(path):
        data = path.read_bytes()
        count = struct.unpack(">H", data[4:6])[0]
        tables = {}
        for i in range(count):
            off = 12 + i * 16
            tag = data[off:off + 4].decode("latin-1")
            start = struct.unpack(">I", data[off + 8:off + 12])[0]
            tables[tag] = start
        base = tables["cmap"]
        found = set()
        for i in range(struct.unpack(">H", data[base + 2:base + 4])[0]):
            rec = base + 4 + i * 8
            sub = base + struct.unpack(">I", data[rec + 4:rec + 8])[0]
            if struct.unpack(">H", data[sub:sub + 2])[0] != 4:
                continue
            seg_x2 = struct.unpack(">H", data[sub + 6:sub + 8])[0]
            segs = seg_x2 // 2
            ends = struct.unpack(f">{segs}H", data[sub + 14:sub + 14 + seg_x2])
            at = sub + 16 + seg_x2
            starts = struct.unpack(f">{segs}H", data[at:at + seg_x2])
            for first, last in zip(starts, ends):
                if first != 0xFFFF:
                    found.update(range(first, min(last, first + 2000) + 1))
        return found

    russian = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
    for family, files in fonts.FONT_FILES.items():
        points = codepoints(fonts.FONTS_DIR / files[0])
        missing = [ch for ch in russian if ord(ch) not in points]
        assert not missing, f"{family}: нет букв {''.join(missing[:6])}"


def test_licences_lie_next_to_the_fonts():
    """OFL требует прикладывать текст лицензии к каждой копии."""
    from narezka.core import fonts

    licences = fonts.FONTS_DIR / "licenses"
    names = {p.name for p in licences.glob("*.txt")}
    assert {"Montserrat-OFL.txt", "Roboto-OFL.txt", "GolosText-OFL.txt"} <= names
