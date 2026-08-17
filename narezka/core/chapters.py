"""Таймкоды длинной нарезки — оглавление для описания под видео.

BAZA.md §20. Двадцать минут без оглавления смотрят с начала или не смотрят
вовсе: зритель не видит, что внутри, и не может вернуться к нужному месту.
Площадки читают такой список из описания и рисуют главы на полосе проигрывания.

**Откуда берутся названия.** У подборки лучших моментов они уже есть: стадия
metadata написала заголовок каждому клипу, и брать их заново значило бы
платить за то, что лежит рядом. У сюжетной нарезки готовых названий нет —
там куски это части одного занятия, и подписать их может только модель.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Chapter:
    """Глава: где начинается в готовом ролике и как называется."""

    at: float
    title: str

    def as_dict(self) -> dict[str, Any]:
        return {"at": round(self.at, 2), "title": self.title}


def timecode(seconds: float) -> str:
    """Время в том виде, в каком его читают площадки.

    Часы появляются только когда они есть: «1:05:00» для длинного ролика
    и «5:00» для короткого. Ведущий ноль в минутах обязателен — без него
    часть площадок список не распознаёт.
    """
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def build(
    pieces: list[tuple[float, float]],
    titles: dict[int, str] | None = None,
    *,
    min_gap: float = 30.0,
) -> list[Chapter]:
    """Собирает оглавление по кускам готового ролика.

    `titles` — название для куска по его номеру. Кусок без названия
    присоединяется к предыдущей главе, а не получает пустую строку:
    глава «—» хуже её отсутствия.

    Главы ближе `min_gap` друг к другу сливаются: площадки не показывают
    оглавление, где главы короче минуты, а список из тридцати строк по
    двадцать секунд бесполезен и человеку.
    """
    names = titles or {}
    chapters: list[Chapter] = []
    at = 0.0

    for index, (start, end) in enumerate(pieces):
        title = (names.get(index) or "").strip()
        if title and (not chapters or at - chapters[-1].at >= min_gap):
            chapters.append(Chapter(at, title))
        at += max(0.0, end - start)

    # Первая глава обязана начинаться с нуля: площадки отвергают список,
    # где первый таймкод не 0:00, и оглавление не показывается вовсе.
    if chapters and chapters[0].at > 0:
        chapters[0] = Chapter(0.0, chapters[0].title)
    return chapters


def as_text(chapters: list[Chapter], total: float) -> str:
    """Оглавление строками «начало-конец — название».

    Конец каждой главы это начало следующей: показывать промежуток нагляднее,
    чем одну точку, — сразу видно, сколько длится кусок.
    """
    if not chapters:
        return ""

    lines = []
    for index, chapter in enumerate(chapters):
        finish = chapters[index + 1].at if index + 1 < len(chapters) else total
        lines.append(f"{timecode(chapter.at)}-{timecode(finish)} — {chapter.title}")
    return "\n".join(lines)
