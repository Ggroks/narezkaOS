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
    assert f",{fonts.FALLBACK_FAMILY}," in ass


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
