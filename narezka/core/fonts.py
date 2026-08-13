"""Шрифты субтитров, лежащие в репозитории.

BAZA.md §60 и §62. До этого стиль называл шрифт по имени семейства, и его
подбирал fontconfig на машине, где идёт рендер. Это тихо ломало два
требования сразу:

- **одинаковый результат везде.** На другой машине того же имени может не
  быть, и libass молча подставит что попало — тот же ролик выйдет другим;
- **кириллица.** Подстановка может не содержать кириллических начертаний,
  и вместо текста получаются пустые прямоугольники. Заметно это только
  на готовом видео, когда рендер уже потрачен.

Поэтому шрифты лежат в `assets/fonts` вместе с лицензией, а libass
получает каталог явным параметром `fontsdir` и берёт их оттуда.
"""

from __future__ import annotations

from pathlib import Path

#: Каталог со шрифтами: два уровня вверх от этого файла — корень репозитория.
FONTS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"

#: Семейство → файл. Ключи совпадают с полем `font` у стиля субтитров.
FONT_FILES: dict[str, tuple[str, ...]] = {
    "DejaVu Sans": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    "DejaVu Serif": ("DejaVuSerif.ttf",),
}

#: Чем подменяется семейство, которого нет в поставке. Пустой кадр с текстом
#: лучше, чем прямоугольники вместо букв.
FALLBACK_FAMILY = "DejaVu Sans"


def fonts_dir() -> Path:
    return FONTS_DIR


def is_vendored(family: str) -> bool:
    """Есть ли семейство в поставке — со всеми файлами на месте."""
    files = FONT_FILES.get(family)
    if not files:
        return False
    return all((FONTS_DIR / name).is_file() for name in files)


def resolve_family(family: str) -> str:
    """Семейство, которое можно писать в стиль ASS."""
    return family if is_vendored(family) else FALLBACK_FAMILY


def missing_files() -> list[str]:
    """Файлы, объявленные в поставке, но отсутствующие на диске."""
    return [
        name
        for names in FONT_FILES.values()
        for name in names
        if not (FONTS_DIR / name).is_file()
    ]


def escape_for_filter(path: Path) -> str:
    """Путь внутри строки фильтра ffmpeg.

    Разделитель параметров — двоеточие, а обратный слэш экранирует. Оба
    встречаются в обычных путях, поэтому экранируются оба, иначе фильтр
    разберётся не так и ошибка вылезет только при рендере.
    """
    return str(path).replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")
