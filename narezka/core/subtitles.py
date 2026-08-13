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


@dataclass(frozen=True)
class SubtitleStyle:
    """Пресет оформления (§18: минимум три штуки)."""

    name: str
    font: str = "DejaVu Sans"
    font_size: int = 64
    #: Цвет обычного текста и цвет текущего слова, в формате &HBBGGRR.
    primary: str = "&H00FFFFFF"
    highlight: str = "&H0033D6FF"
    outline_colour: str = "&H00000000"
    back_colour: str = "&H80000000"
    outline: float = 3.5
    shadow: float = 1.0
    bold: bool = True
    #: Доли кадра, которые нельзя занимать.
    bottom_safe: float = DEFAULT_BOTTOM_SAFE
    side_safe: float = DEFAULT_SIDE_SAFE
    max_chars_per_line: int = 22
    max_lines: int = 2
    #: Пауза, по которой реплика разрывается принудительно.
    pause_seconds: float = 0.55
    max_cue_seconds: float = 3.5


STYLES: dict[str, SubtitleStyle] = {
    "STYLE_1": SubtitleStyle(name="STYLE_1"),
    "STYLE_2": SubtitleStyle(
        name="STYLE_2",
        font="DejaVu Sans",
        font_size=72,
        highlight="&H004CFF4C",
        outline=4.0,
        max_chars_per_line=18,
    ),
    "STYLE_3": SubtitleStyle(
        name="STYLE_3",
        font="DejaVu Serif",
        font_size=58,
        highlight="&H00FFFFFF",
        primary="&H00C8C8C8",
        outline=2.5,
        bold=False,
        max_lines=3,
    ),
}


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
    cues: list[Cue] = []
    current: list[dict[str, Any]] = []

    for word in words:
        if current:
            gap = word["start"] - current[-1]["end"]
            length = len(" ".join(w["word"] for w in current)) + 1 + len(word["word"])
            duration = word["end"] - current[0]["start"]
            if gap >= style.pause_seconds or length > capacity or duration > style.max_cue_seconds:
                cues.append(Cue(current[0]["start"], current[-1]["end"], current))
                current = []
        current.append(word)

    if current:
        cues.append(Cue(current[0]["start"], current[-1]["end"], current))
    return cues


def wrap_words(words: list[dict[str, Any]], max_chars: int) -> list[list[dict[str, Any]]]:
    """Разбивает слова реплики на экранные строки по ширине."""
    lines: list[list[dict[str, Any]]] = []
    line: list[dict[str, Any]] = []
    for word in words:
        candidate = len(" ".join(w["word"] for w in line)) + (1 if line else 0) + len(word["word"])
        if line and candidate > max_chars:
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
    margin_v = int(height * style.bottom_safe)
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
        f"100,100,0,0,1,{style.outline},{style.shadow},2,"
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
        lines = wrap_words(cue.words, style.max_chars_per_line)[: style.max_lines]
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
