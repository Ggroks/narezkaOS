"""Сборка субтитров ASS с подсветкой текущего слова.

BAZA.md §18 и §60. Основа — пословная разметка из распознавания: реплики
режутся по паузам и смыслу, а не по равным интервалам, иначе строка рвёт
фразу пополам.

Подсветка слова делается тегами `\\k` — это штатный караоке-механизм ASS,
libass рисует его сам, без покадровой генерации.

Модуль чистый: на входе слова и стиль, на выходе строка. Работа с файлами
и клипами — в стадии.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from narezka.core.fonts import resolve_family

#: Интерфейсы TikTok, Shorts и Reels перекрывают низ кадра подписями и кнопками,
#: а правый край — панелью действий (§60). Значения приблизительные: они меняются
#: вместе с приложениями платформ, поэтому вынесены в стиль.
DEFAULT_BOTTOM_SAFE = 0.22
DEFAULT_SIDE_SAFE = 0.08


def ass_colour(value: str) -> str:
    """Цвет из интерфейса в цвет ASS.

    `#FFCC00` → `&H0000CCFF`. Порядок байтов обратный привычному: ASS пишет
    синий-зелёный-красный, а не красный-зелёный-синий. Перепутать легко,
    и ошибка выглядит как «сделал жёлтый, получил синий».
    """
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    # Проверка не только длины: «жёлтый» — ровно шесть знаков, и без разбора
    # шестнадцатеричного числа он проходил насквозь, превращаясь в цвет
    # «&H00ЫЙЛТЖЁ». Ошибка всплыла бы на готовом ролике.
    if len(text) != 6 or not all(ch in "0123456789abcdefABCDEF" for ch in text):
        raise ValueError(f"цвет должен быть вида #RRGGBB, получено «{value}»")
    red, green, blue = text[0:2], text[2:4], text[4:6]
    return f"&H00{blue}{green}{red}".upper()


def hex_colour(value: str) -> str:
    """Обратно — чтобы интерфейс показал текущий цвет в своём поле."""
    text = value.strip().upper().removeprefix("&H")
    if len(text) == 8:
        text = text[2:]
    if len(text) != 6:
        return "#FFFFFF"
    blue, green, red = text[0:2], text[2:4], text[4:6]
    return f"#{red}{green}{blue}"


#: Где стоят субтитры. ASS считает выравнивание цифрами: 2 — низ по центру,
#: 5 — середина, 8 — верх. Наружу отдаются слова, а не цифры.
POSITIONS: dict[str, int] = {"bottom": 2, "middle": 5, "top": 8}


@dataclass(frozen=True)
class SubtitleStyle:
    """Оформление субтитров.

    Часть полей задаётся человеком в интерфейсе, часть — техническая
    и в интерфейс не выведена: длительность реплики, пауза разрыва,
    безопасные поля платформ. Их правка нужна редко, а ошибиться в них
    легко — реплики начнут рваться посреди фразы.
    """

    name: str
    font: str = "Montserrat"
    font_size: int = 64
    #: Цвет обычного текста и цвет текущего слова, в формате &HBBGGRR.
    primary: str = "&H00FFFFFF"
    highlight: str = "&H0033D6FF"
    outline_colour: str = "&H00000000"
    back_colour: str = "&H80000000"
    outline: float = 3.5
    shadow: float = 1.0
    bold: bool = True
    #: Где стоят субтитры: bottom | middle | top.
    position: str = "bottom"
    #: Доли кадра, которые нельзя занимать.
    bottom_safe: float = DEFAULT_BOTTOM_SAFE
    side_safe: float = DEFAULT_SIDE_SAFE
    #: Предел строки. Два разных ограничения, и срабатывает то, что раньше:
    #: по словам его задают на глаз («не больше трёх слов в строке»),
    #: по символам оно точнее держит ширину — «Здравствуйте, уважаемые»
    #: это два слова, но полторы строки.
    max_words_per_line: int = 4
    max_chars_per_line: int = 22
    max_lines: int = 2
    #: Пауза, по которой реплика разрывается принудительно.
    pause_seconds: float = 0.55
    max_cue_seconds: float = 3.5

    @property
    def alignment(self) -> int:
        return POSITIONS.get(self.position, 2)


#: Готовые наборы. Имена человеческие: «STYLE_2» не говорит ничего, пока
#: не увидишь. Различаются тем, что видно на кадре, а не числами в файле.
PRESETS: dict[str, dict[str, Any]] = {
    "classic": {
        "title": "Классические",
        "note": "Белые с жёлтой подсветкой, снизу. Подходит почти всему",
        "style": {"font": "Montserrat"},
    },
    "loud": {
        "title": "Крупные",
        "note": "Больше и жирнее, по два-три слова в строке — для динамичных нарезок",
        "style": {
            "font": "Montserrat",
            "font_size": 78,
            "outline": 4.5,
            "highlight": "&H004CFF4C",
            "max_words_per_line": 3,
            "max_chars_per_line": 18,
        },
    },
    "calm": {
        "title": "Спокойные",
        "note": "Тонкая обводка, засечки, без выделения цветом — для разговорных",
        "style": {
            "font": "Golos Text",
            "font_size": 58,
            "primary": "&H00C8C8C8",
            "highlight": "&H00FFFFFF",
            "outline": 2.5,
            "bold": False,
            "max_lines": 3,
            "max_words_per_line": 6,
        },
    },
    "center": {
        "title": "По центру кадра",
        "note": "Крупные посреди экрана — когда внизу происходит главное",
        "style": {
            "font": "Inter",
            "font_size": 72,
            "position": "middle",
            "max_words_per_line": 3,
            "max_chars_per_line": 16,
        },
    },
    "one_word": {
        "title": "По одному слову",
        "note": "Слово за словом крупно — удерживает внимание, но читается тяжелее",
        "style": {
            "font": "Oswald",
            "font_size": 92,
            "outline": 5.0,
            "max_words_per_line": 1,
            "max_chars_per_line": 14,
            "max_lines": 1,
            "max_cue_seconds": 1.2,
        },
    },
}

#: Набор по умолчанию.
DEFAULT_PRESET = "classic"

#: Старые имена стилей. Записи, сделанные до пресетов, ссылаются на них,
#: и молча подставить другой стиль значило бы поменять готовые ролики.
LEGACY = {"STYLE_1": "classic", "STYLE_2": "loud", "STYLE_3": "calm"}


def preset_style(name: str, overrides: dict[str, Any] | None = None) -> SubtitleStyle:
    """Стиль по имени пресета с правками поверх.

    Правки применяются полем за полем, а не заменой целиком: человек меняет
    цвет и ждёт, что остальное останется от выбранного набора.
    """
    key = LEGACY.get(name, name)
    preset = PRESETS.get(key)
    if preset is None:
        raise KeyError(f"неизвестный пресет субтитров '{name}'; есть: {', '.join(PRESETS)}")

    fields = {**preset["style"]}
    for field_name, value in (overrides or {}).items():
        if value is None or field_name not in SubtitleStyle.__dataclass_fields__:
            continue
        fields[field_name] = value
    return SubtitleStyle(name=key, **fields)


def describe_presets() -> list[dict[str, Any]]:
    """Пресеты для интерфейса: имя, что это и как выглядит."""
    return [
        {
            "name": key,
            "title": preset["title"],
            "note": preset["note"],
            "style": preset_style(key).__dict__,
        }
        for key, preset in PRESETS.items()
    ]


#: Совместимость: часть кода и тестов зовёт словарь стилей.
STYLES: dict[str, SubtitleStyle] = {key: preset_style(key) for key in PRESETS}
STYLES.update({old: preset_style(new) for old, new in LEGACY.items()})


@dataclass
class Cue:
    """Одна реплика на экране."""

    start: float
    end: float
    words: list[dict[str, Any]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w["word"] for w in self.words)


def group_words(
    words: list[dict[str, Any]],
    style: SubtitleStyle,
) -> list[Cue]:
    """Режет поток слов на реплики.

    Разрыв происходит по трём причинам, в порядке проверки: пауза между словами,
    переполнение по символам, переполнение по длительности. Пауза идёт первой,
    потому что она соответствует границе мысли, а остальные — техническим
    ограничениям экрана.
    """
    if not words:
        return []

    capacity = style.max_chars_per_line * style.max_lines
    word_capacity = max(style.max_words_per_line, 1) * style.max_lines
    cues: list[Cue] = []
    current: list[dict[str, Any]] = []

    for word in words:
        if current:
            gap = word["start"] - current[-1]["end"]
            length = len(" ".join(w["word"] for w in current)) + 1 + len(word["word"])
            duration = word["end"] - current[0]["start"]
            if (
                gap >= style.pause_seconds
                or length > capacity
                or len(current) + 1 > word_capacity
                or duration > style.max_cue_seconds
            ):
                cues.append(Cue(current[0]["start"], current[-1]["end"], current))
                current = []
        current.append(word)

    if current:
        cues.append(Cue(current[0]["start"], current[-1]["end"], current))
    return cues


def wrap_words(
    words: list[dict[str, Any]], max_chars: int, max_words: int = 0
) -> list[list[dict[str, Any]]]:
    """Разбивает слова реплики на экранные строки.

    Два предела, и срабатывает тот, что раньше. По словам его задают на глаз
    («не больше трёх в строке»), по символам он точнее держит ширину:
    «Здравствуйте, уважаемые» — это два слова, но полторы строки.
    """
    lines: list[list[dict[str, Any]]] = []
    line: list[dict[str, Any]] = []
    for word in words:
        candidate = len(" ".join(w["word"] for w in line)) + (1 if line else 0) + len(word["word"])
        too_long = line and candidate > max_chars
        too_many = line and max_words > 0 and len(line) >= max_words
        if too_long or too_many:
            lines.append(line)
            line = []
        line.append(word)
    if line:
        lines.append(line)
    return lines


def _timestamp(seconds: float) -> str:
    """Формат ASS: часы:минуты:секунды.сотые, часы без ведущего нуля."""
    seconds = max(seconds, 0.0)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole = seconds % 60
    return f"{hours:d}:{minutes:02d}:{whole:05.2f}"


def _karaoke_line(words: list[dict[str, Any]], highlight: str, primary: str) -> str:
    """Строка с посимвольной подсветкой текущего слова.

    Каждое слово получает длительность в сотых долях секунды. `\\kf` заливает
    слово постепенно, что читается мягче, чем мгновенное переключение цвета.
    """
    parts = []
    for word in words:
        centiseconds = max(int(round((word["end"] - word["start"]) * 100)), 1)
        parts.append(f"{{\\kf{centiseconds}}}{_escape(word['word'])}")
    return " ".join(parts)


def _escape(text: str) -> str:
    """Экранирует то, что libass принял бы за разметку."""
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def build_ass(
    words: list[dict[str, Any]],
    *,
    style: SubtitleStyle,
    width: int,
    height: int,
    time_offset: float = 0.0,
) -> str:
    """Собирает готовый файл ASS.

    `time_offset` вычитается из времён: субтитры клипа отсчитываются от его
    начала, а не от начала исходного видео.
    """
    # Поле снизу нужно только нижнему положению: у верхнего оно отодвигает
    # от верхнего края, у среднего не значит ничего.
    margin_v = int(height * style.bottom_safe) if style.position != "middle" else 0
    margin_h = int(width * style.side_safe)

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",  # переносы задаём сами, автоматические не нужны
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # SecondaryColour — цвет ещё не прозвучавшей части при караоке,
        # PrimaryColour — уже прозвучавшей. Подсветка идёт в Primary.
        f"Style: Main,{resolve_family(style.font)},{style.font_size},{style.highlight},{style.primary},"
        f"{style.outline_colour},{style.back_colour},{-1 if style.bold else 0},0,0,0,"
        f"100,100,0,0,1,{style.outline},{style.shadow},{style.alignment},"
        f"{margin_h},{margin_h},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    events = []
    for cue in group_words(words, style):
        start = cue.start - time_offset
        end = cue.end - time_offset
        if end <= 0:
            continue
        lines = wrap_words(
            cue.words, style.max_chars_per_line, style.max_words_per_line
        )[: style.max_lines]
        body = "\\N".join(_karaoke_line(line, style.highlight, style.primary) for line in lines)
        events.append(
            f"Dialogue: 0,{_timestamp(start)},{_timestamp(end)},Main,,0,0,0,,{body}"
        )

    return "\n".join(header + events) + "\n"


def words_in_range(
    segments: list[dict[str, Any]],
    start: float,
    end: float,
) -> list[dict[str, Any]]:
    """Слова, попадающие во временной отрезок.

    Подозрительные сегменты (§57) исключаются: выдуманный текст не должен
    попасть в кадр.
    """
    words: list[dict[str, Any]] = []
    for segment in segments:
        if segment.get("suspect"):
            continue
        for word in segment.get("words") or []:
            if word["end"] > start and word["start"] < end:
                words.append(word)
    return sorted(words, key=lambda w: w["start"])
