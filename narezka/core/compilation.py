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


#: Доля эпизода, которую можно выбросить при уплотнении. Больше половины —
#: это уже не уплотнение, а пересказ: связки теряются, и зритель перестаёт
#: понимать, как одно вытекает из другого.
MAX_TRIM_SHARE = 0.5

#: Наименьший кусок, который имеет смысл оставлять. Отрезки по паре секунд
#: превращают ролик в мельтешение, даже если каждый из них сам по себе хорош.
MIN_PIECE = 8.0


def condense(
    episode_start: float,
    episode_end: float,
    keep: list[tuple[float, float]],
    target_seconds: float,
) -> list[tuple[float, float]]:
    """Уплотняет эпизод до нужной длительности, сохраняя порядок.

    `keep` — отрезки, которые стоит оставить обязательно: найденные моменты,
    оживление чата, места со смехом. Всё остальное внутри эпизода считается
    связкой и режется первым.

    **Почему не выбрасываем связки целиком.** Сюжет держится на переходах:
    выкинув всё между яркими местами, получим ту же подборку моментов, от
    которой сюжетная нарезка и отличается. Поэтому связки сокращаются, а не
    исчезают: между сохранёнными кусками остаётся то, что их соединяет,
    пока хватает бюджета.
    """
    total = episode_end - episode_start
    if total <= 0:
        return []
    if target_seconds >= total:
        return [(episode_start, episode_end)]

    floor = total * (1.0 - MAX_TRIM_SHARE)
    budget = max(target_seconds, floor)

    # Обязательные куски внутри эпизода, слитые и упорядоченные.
    inside = sorted(
        (max(a, episode_start), min(b, episode_end))
        for a, b in keep
        if min(b, episode_end) - max(a, episode_start) >= MIN_PIECE
    )
    merged: list[list[float]] = []
    for a, b in inside:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    kept = sum(b - a for a, b in merged)
    if not merged:
        # Обязательных кусков нет — берём начало эпизода: у него есть завязка,
        # а обрывать с середины значит лишить зрителя входа в историю.
        return [(episode_start, episode_start + budget)]

    # Оставшийся бюджет распределяется на связки между кусками поровну:
    # предпочесть одну длинную связку нескольким коротким значило бы
    # оборвать переходы в остальных местах.
    gaps = []
    previous = episode_start
    for a, b in merged:
        if a - previous > 0:
            gaps.append((previous, a))
        previous = b
    if episode_end - previous > 0:
        gaps.append((previous, episode_end))

    spare = max(0.0, budget - kept)
    pieces: list[tuple[float, float]] = [(a, b) for a, b in merged]

    # Бюджет раздаётся связкам поровну, но короткая связка не может выбрать
    # свою долю целиком — остаток от неё уходит остальным. Без перераспределения
    # общая длительность недобирала до заданной: одна короткая связка забирала
    # долю и возвращала её в никуда.
    taken = {index: 0.0 for index in range(len(gaps))}
    left = spare
    pending = set(taken)
    while left > 1e-6 and pending:
        share = left / len(pending)
        progressed = False
        for index in sorted(pending):
            a, b = gaps[index]
            room = (b - a) - taken[index]
            add = min(room, share)
            if add > 1e-6:
                taken[index] += add
                left -= add
                progressed = True
            if (b - a) - taken[index] <= 1e-6:
                pending.discard(index)
        if not progressed:
            break

    trimmed_gaps = []
    for index, (a, b) in enumerate(gaps):
        length = taken[index]
        if length >= MIN_PIECE:
            # Связка берётся с конца: ближе к следующему куску, то есть
            # к тому, ради чего она нужна.
            trimmed_gaps.append((b - length, b))

    # Соприкасающиеся куски склеиваются: рез там, где ничего не вырезано, —
    # лишняя работа рендеру и лишний шов в звуке.
    ordered_pieces = sorted(pieces + trimmed_gaps)
    result: list[list[float]] = []
    for a, b in ordered_pieces:
        if result and a - result[-1][1] < 0.05:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return [(a, b) for a, b in result]
