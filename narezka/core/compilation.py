"""Сборка длинной компиляции: порядок моментов, а не просто склейка.

BAZA.md §20, §21. Отличие от нарезки шортсов в том, что здесь важен не
каждый момент по отдельности, а **последовательность**: зритель смотрит
двадцать минут подряд, и порядок решает, досмотрит ли он их.

**Структура, а не рейтинг.** Простая сортировка по оценке даёт худший из
возможных порядков: сильнейшее в начале, дальше по нисходящей, и зритель
уходит ровно тогда, когда стало скучнее. Поэтому роли распределяются:

- **зацепка** — сильный момент первым, чтобы человек остался;
- **середина** — в хронологическом порядке, чтобы происходящее читалось
  как история, а не как перемешанная лента;
- **кульминация** — сильнейший момент ближе к концу, ради него смотрят;
- **концовка** — спокойный момент последним, чтобы ролик завершился,
  а не оборвался.

**Почему середина хронологическая.** §20 требует сохранять контекст между
сценами. Если стример сначала получил задание, потом его выполнял, а потом
праздновал — перемешать это значит показать праздник до задания. Внутри
одной записи хронология и есть контекст, и она даётся бесплатно.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Доли целевой длительности, отводимые на края. Зацепка короткая — это
#: обещание, а не рассказ; концовка тоже, иначе ролик провисает на выходе.
HOOK_SHARE = 0.08
ENDING_SHARE = 0.07

#: Насколько можно превысить заданную длительность, добирая последний момент.
#: Резать момент пополам ради ровного числа минут хуже, чем выйти за него:
#: оборванная реплика заметнее лишней минуты.
OVERSHOOT = 0.15

ROLES = ("hook", "body", "climax", "ending")


@dataclass(frozen=True)
class Part:
    """Момент в компиляции с назначенной ролью."""

    clip: dict[str, Any]
    role: str
    #: Место в готовой компиляции, секунды от её начала.
    at: float

    @property
    def duration(self) -> float:
        return float(self.clip.get("duration") or (self.clip["end"] - self.clip["start"]))


@dataclass(frozen=True)
class Compilation:
    parts: list[Part]

    @property
    def duration(self) -> float:
        return sum(part.duration for part in self.parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "duration": round(self.duration, 2),
            "parts": [
                {
                    "index": part.clip.get("index"),
                    "role": part.role,
                    "at": round(part.at, 2),
                    "start": part.clip["start"],
                    "end": part.clip["end"],
                    "score": part.clip.get("interest_score"),
                }
                for part in self.parts
            ],
        }


def _score(clip: dict[str, Any]) -> float:
    value = clip.get("interest_score")
    if value is None:
        value = clip.get("provisional_score")
    return float(value or 0.0)


def _duration(clip: dict[str, Any]) -> float:
    return float(clip.get("duration") or (clip["end"] - clip["start"]))


def select(clips: list[dict[str, Any]], target_seconds: float) -> list[dict[str, Any]]:
    """Отбирает моменты под заданную длительность — сильнейшие вперёд.

    Добор идёт по оценке, а не по хронологии: в компиляцию должно попасть
    лучшее, а расставит их уже `arrange`.
    """
    if target_seconds <= 0:
        return []

    limit = target_seconds * (1.0 + OVERSHOOT)
    chosen: list[dict[str, Any]] = []
    total = 0.0
    for clip in sorted(clips, key=_score, reverse=True):
        length = _duration(clip)
        if length <= 0:
            continue
        if total + length > limit and chosen:
            continue
        chosen.append(clip)
        total += length
        if total >= target_seconds:
            break
    return chosen


def arrange(clips: list[dict[str, Any]], target_seconds: float) -> Compilation:
    """Расставляет отобранные моменты по ролям.

    Порядок ролей: зацепка, середина в хронологии, кульминация, концовка.
    Сильнейший момент уходит в кульминацию, а не в зацепку: в начале нужен
    сильный, но не лучший — иначе дальше только вниз.
    """
    chosen = select(clips, target_seconds)
    if not chosen:
        return Compilation([])
    if len(chosen) == 1:
        return Compilation([Part(chosen[0], "climax", 0.0)])

    by_score = sorted(chosen, key=_score, reverse=True)
    climax = by_score[0]
    hook = by_score[1] if len(by_score) > 1 else None

    rest = [c for c in chosen if c is not climax and c is not hook]

    # Концовка — самый спокойный из оставшихся: ролик должен завершиться,
    # а не оборваться на крике. Если оставшихся мало, обходимся без неё.
    ending = None
    if len(rest) >= 2:
        ending = min(rest, key=_score)
        rest = [c for c in rest if c is not ending]

    # Середина строго в хронологии — это и есть сохранение контекста.
    rest.sort(key=lambda c: float(c["start"]))

    ordered: list[tuple[dict[str, Any], str]] = []
    if hook is not None:
        ordered.append((hook, "hook"))
    ordered.extend((clip, "body") for clip in rest)
    ordered.append((climax, "climax"))
    if ending is not None:
        ordered.append((ending, "ending"))

    parts: list[Part] = []
    at = 0.0
    for clip, role in ordered:
        parts.append(Part(clip, role, at))
        at += _duration(clip)
    return Compilation(parts)
