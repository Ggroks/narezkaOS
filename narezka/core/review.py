"""Разметка кандидатов человеком.

BAZA.md §35 и §63. Каждое «годится» и «не годится» — готовая обучающая метка,
и она накапливается сама собой при обычной работе, без отдельных усилий.
Метрики опубликованных клипов ценнее, но приходят через недели; решения
из обзора доступны с первого дня.

Два правила, без которых накопленное окажется бесполезным:

1. **Признаки замораживаются в момент решения.** Кандидат пересчитывается при
   каждом изменении стадии, и через месяц уже не восстановить, на какие именно
   числа человек смотрел, когда нажимал «годится». Поэтому решение хранит
   снимок кандидата целиком.
2. **Правка границ не затирает исходные.** Насколько человек подвинул границу —
   отдельный сигнал: он говорит, что автоматика систематически ошибается
   в одну сторону.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any, Literal

Verdict = Literal["accept", "reject"]

SCHEMA_VERSION = 1

#: Насколько граница считается «подвинутой». Ниже — округление при
#: перетаскивании, а не осмысленная правка.
NUDGE_EPSILON = 0.05


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def empty() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "decisions": []}


def by_index(review: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for entry in review.get("decisions", []):
        index = entry.get("index")
        if isinstance(index, int):
            result[index] = entry
    return result


def record(
    review: dict[str, Any],
    *,
    index: int,
    candidate: dict[str, Any],
    verdict: Verdict | None = None,
    start: float | None = None,
    end: float | None = None,
) -> dict[str, Any]:
    """Записывает или обновляет решение по одному кандидату.

    `verdict=None` оставляет прежний вердикт — так границы можно править,
    не меняя оценки, и наоборот.
    """
    existing = by_index(review).get(index, {})

    # Исходные границы берутся у кандидата один раз: после первой правки
    # «оригиналом» должно остаться то, что предложила автоматика.
    original = existing.get("original") or {
        "start": float(candidate["start"]),
        "end": float(candidate["end"]),
    }

    new_start = float(start if start is not None else existing.get("start", candidate["start"]))
    new_end = float(end if end is not None else existing.get("end", candidate["end"]))
    if new_end <= new_start:
        raise ValueError("конец клипа должен быть позже начала")

    entry = {
        "index": index,
        "verdict": verdict if verdict is not None else existing.get("verdict"),
        "at": _now(),
        "start": round(new_start, 3),
        "end": round(new_end, 3),
        "original": original,
        # Снимок делается один раз — при первом решении. Иначе он поедет
        # вместе с пересчётом стадии и перестанет отражать то, что видел
        # человек.
        "snapshot": existing.get("snapshot") or copy.deepcopy(candidate),
    }

    decisions = [d for d in review.get("decisions", []) if d.get("index") != index]
    decisions.append(entry)
    decisions.sort(key=lambda d: d["index"])
    return {"schema_version": SCHEMA_VERSION, "decisions": decisions}


def forget(review: dict[str, Any], index: int) -> dict[str, Any]:
    """Убирает решение — кандидат снова неразмеченный."""
    decisions = [d for d in review.get("decisions", []) if d.get("index") != index]
    return {"schema_version": SCHEMA_VERSION, "decisions": decisions}


def merge(candidates: list[dict[str, Any]], review: dict[str, Any]) -> list[dict[str, Any]]:
    """Кандидаты вместе с решениями — то, что показывает интерфейс.

    Границы отдаются уже поправленные: интерфейс не должен сам решать,
    какие из двух показывать.
    """
    decisions = by_index(review)
    result = []
    for index, candidate in enumerate(candidates):
        entry = decisions.get(index)
        merged = {**candidate, "index": index}
        if entry:
            merged["verdict"] = entry.get("verdict")
            merged["decided_at"] = entry.get("at")
            merged["start"] = entry["start"]
            merged["end"] = entry["end"]
            merged["original"] = entry["original"]
            merged["edited"] = _moved(entry)
        else:
            merged["verdict"] = None
            merged["edited"] = False
        merged["duration"] = round(merged["end"] - merged["start"], 2)
        result.append(merged)
    return result


def _moved(entry: dict[str, Any]) -> bool:
    original = entry.get("original") or {}
    return (
        abs(entry["start"] - original.get("start", entry["start"])) > NUDGE_EPSILON
        or abs(entry["end"] - original.get("end", entry["end"])) > NUDGE_EPSILON
    )


def stats(merged: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = sum(1 for c in merged if c["verdict"] == "accept")
    rejected = sum(1 for c in merged if c["verdict"] == "reject")
    return {
        "total": len(merged),
        "accepted": accepted,
        "rejected": rejected,
        "undecided": len(merged) - accepted - rejected,
        "edited": sum(1 for c in merged if c.get("edited")),
    }
