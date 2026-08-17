"""Поиск эпизодов: связных занятий с началом и концом.

BAZA.md §20. Сюжетная нарезка отличается от подборки лучших моментов тем,
что сюжет **не придумывается нами, а уже есть в записи**: раунд от старта
до победы, просмотр видео от «давайте глянем» до реакции, путь из точки А
в точку Б. Наша задача — найти границы такого занятия и уплотнить его.

**Почему границы ищет модель, а не сигналы.** Смена темы — это смысл, а не
всплеск. Стример может замолчать посреди раунда и болтать между раундами;
громкость и чат тут ничего не скажут. Единственный источник, где написано,
чем человек занят, — расшифровка речи.

**Почему кусками с перекрытием.** Пятичасовая запись это 37 тысяч слов, и
целиком их в модель не отправить. Куски берутся крупные — час записи, —
потому что эпизод длиной сорок минут внутри получасового окна не поместится
и будет разорван. Перекрытие нужно, чтобы граница на стыке кусков нашлась
хотя бы в одном из них.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

#: Сколько записи уходит в один запрос. Час: эпизоды бывают до сорока минут,
#: и окно должно вмещать эпизод целиком вместе с началом и концом.
CHUNK_SECONDS = 3600.0

#: Перекрытие соседних кусков. Десять минут: эпизод, начавшийся у самого
#: края окна, должен целиком попасть в следующее.
CHUNK_OVERLAP = 600.0

#: Границы длительности эпизода. Короче пяти минут — это момент, а не
#: занятие; длиннее часа — обычно не эпизод, а весь стрим одной темы.
MIN_EPISODE = 300.0
MAX_EPISODE = 3600.0

#: Насколько эпизоды должны перекрываться, чтобы считаться одним и тем же.
#: Половина: найденное в двух соседних окнах описывает одно занятие, и
#: показывать его дважды значит врать о числе вариантов.
MERGE_OVERLAP = 0.5


@dataclass(frozen=True)
class Episode:
    start: float
    end: float
    title: str
    #: Чем занят человек: одна фраза, зачем это смотреть.
    summary: str
    #: Оценка цельности: есть ли у куска начало, развитие и завершение.
    coherence: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration": round(self.duration, 2),
            "title": self.title,
            "summary": self.summary,
            "coherence": round(self.coherence, 3),
        }


def chunks(duration: float, size: float = CHUNK_SECONDS, overlap: float = CHUNK_OVERLAP):
    """Окна записи с перекрытием. Последнее не короче половины размера.

    Огрызок в пять минут в конце записи не даст модели ничего, кроме шанса
    выдумать эпизод из обрывка, — он прирастает к предыдущему окну.
    """
    if duration <= size:
        return [(0.0, duration)]

    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        end = min(start + size, duration)
        windows.append((start, end))
        if end >= duration:
            break
        start += size - overlap

    if len(windows) > 1 and windows[-1][1] - windows[-1][0] < size / 2:
        last = windows.pop()
        windows[-1] = (windows[-1][0], last[1])
    return windows


def transcript_digest(segments: list[dict[str, Any]], start: float, end: float, step: float = 30.0) -> str:
    """Сжатая расшифровка окна: по строке на каждые полминуты.

    Полностью текст не отправляется намеренно. Модели нужно понять, **чем
    занят человек**, а не разобрать каждое слово; час записи целиком — это
    восемь тысяч слов, из которых девять десятых не влияют на границы темы.
    Строка на полминуты сохраняет ход разговора и укладывается в разумный
    запрос.
    """
    buckets: dict[int, list[str]] = {}
    for segment in segments:
        at = float(segment.get("start", 0.0))
        if not start <= at < end:
            continue
        text = str(segment.get("text", "")).strip()
        if text:
            buckets.setdefault(int((at - start) / step), []).append(text)

    lines = []
    for index in sorted(buckets):
        at = start + index * step
        minutes, seconds = divmod(int(at), 60)
        joined = " ".join(buckets[index])
        lines.append(f"[{minutes}:{seconds:02d}] {joined[:220]}")
    return "\n".join(lines)


def parse(
    answer: str,
    window_start: float,
    window_end: float,
    rejected: list[str] | None = None,
) -> list[Episode]:
    """Разбирает ответ модели. Негодные записи отбрасываются молча.

    Молча потому, что модель регулярно возвращает один-два мусорных пункта
    среди годных, и ронять из-за них всю разметку окна значило бы терять
    работу целиком ради строгости.
    """
    match = re.search(r"\[.*\]", answer, re.S)
    if not match:
        return []
    try:
        raw = json.loads(match.group(0))
    except ValueError:
        return []

    episodes: list[Episode] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue

        # Границы прижимаются к окну: модель иногда выходит за него,
        # пересказывая то, чего в куске не было.
        start = max(window_start, min(start, window_end))
        end = max(window_start, min(end, window_end))
        if not MIN_EPISODE <= end - start <= MAX_EPISODE:
            # Причина отсева записывается: без неё стадия сообщает «ничего
            # не найдено» и там, где модель ответила верно, но слишком
            # мелко, — а это чинится промптом, а не гаданием.
            if rejected is not None:
                length = (end - start) / 60
                rejected.append(f"«{item.get('title', '?')}» — {length:.1f} мин")
            continue

        title = str(item.get("title") or "").strip()
        if not title:
            continue
        episodes.append(Episode(
            start=start, end=end, title=title[:120],
            summary=str(item.get("summary") or "").strip()[:400],
            coherence=max(0.0, min(1.0, float(item.get("coherence") or 0.5))),
        ))
    return episodes


def merge(episodes: list[Episode], overlap: float = MERGE_OVERLAP) -> list[Episode]:
    """Убирает повторы со стыков окон, оставляя более цельный вариант."""
    ordered = sorted(episodes, key=lambda e: (e.start, -e.coherence))
    kept: list[Episode] = []
    for episode in ordered:
        duplicate = None
        for index, existing in enumerate(kept):
            left = max(existing.start, episode.start)
            right = min(existing.end, episode.end)
            shared = max(0.0, right - left)
            if shared >= overlap * min(existing.duration, episode.duration):
                duplicate = index
                break
        if duplicate is None:
            kept.append(episode)
        elif episode.coherence > kept[duplicate].coherence:
            kept[duplicate] = episode
    return sorted(kept, key=lambda e: e.start)
