"""Связка «вектор признаков → фактический результат».

BAZA.md §63 и §40. Отвечает на вопрос, ради которого всё и собиралось:
какие признаки действительно предсказывают результат, а какие мы считаем
важными зря.

Главная опасность этого модуля — самообман. На четырёх клипах любая
корреляция выйдет высокой и ничего не будет значить. Поэтому здесь везде
рядом с числом стоит объём выборки, а ниже порога результат прямо
помечается недостоверным, а не выводится молча (§54).
"""

from __future__ import annotations

import math
from typing import Any

#: Ниже этого числа наблюдений корреляция не сообщается как результат.
#: Взято не из статистической строгости, а из здравого смысла: на пяти
#: точках коэффициент скачет от −1 до 1 при добавлении шестой.
MIN_SAMPLE = 8

#: Метрики, по которым имеет смысл искать связь.
METRICS = ("views", "likes", "comments", "shares", "retention", "ctr")


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Коэффициент линейной связи. None, если считать не из чего."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]

    denominator = math.sqrt(sum(d * d for d in dx) * sum(d * d for d in dy))
    if denominator == 0:
        # Один из рядов постоянный — связи нет, но это не ошибка счёта.
        return None
    return round(sum(a * b for a, b in zip(dx, dy, strict=True)) / denominator, 3)


def factor_correlations(rows: list[dict[str, Any]], metric: str = "views") -> dict[str, Any]:
    """Связь каждого фактора с фактическим результатом."""
    usable = [
        row for row in rows
        if isinstance(row.get(metric), (int, float)) and isinstance(row.get("factors_snapshot"), dict)
    ]

    names = sorted({name for row in usable for name, value in row["factors_snapshot"].items() if value is not None})
    result = {}
    for name in names:
        pairs = [
            (float(row["factors_snapshot"][name]), float(row[metric]))
            for row in usable
            if row["factors_snapshot"].get(name) is not None
        ]
        if len(pairs) < 2:
            continue
        result[name] = {
            "correlation": pearson([p[0] for p in pairs], [p[1] for p in pairs]),
            "sample": len(pairs),
        }

    return {
        "metric": metric,
        "sample": len(usable),
        # Прямой ответ на вопрос «можно ли этому верить», а не молчаливый вывод.
        "reliable": len(usable) >= MIN_SAMPLE,
        "min_sample": MIN_SAMPLE,
        "factors": result,
    }


def correlation_status(correlations: dict[str, Any]) -> str:
    """Почему таблицы связей нет.

    «Метрик нет» и «метрики есть, но точек мало» — разные состояния, и путать
    их нельзя: в первом случае надо вносить данные, во втором просто ждать.
    """
    if correlations["sample"] == 0:
        return "no_data"
    if not correlations["factors"]:
        return "single_point"
    return "ok"


def verdict_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Совпадает ли оценка модели с решением человека.

    Быстрый сигнал: копится с первого дня работы, тогда как метрики
    публикации приходят через недели. Если средняя оценка принятых клипов
    не отличается от отклонённых, модель не различает то, что различает
    человек, — и это видно задолго до первых просмотров.
    """
    accepted = [r["interest_score"] for r in rows if r.get("human_verdict") == "accept" and r.get("interest_score") is not None]
    rejected = [r["interest_score"] for r in rows if r.get("human_verdict") == "reject" and r.get("interest_score") is not None]

    def average(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 3) if values else None

    mean_accepted = average(accepted)
    mean_rejected = average(rejected)
    gap = (
        round(mean_accepted - mean_rejected, 3)
        if mean_accepted is not None and mean_rejected is not None
        else None
    )

    return {
        "accepted": len(accepted),
        "rejected": len(rejected),
        "mean_accepted": mean_accepted,
        "mean_rejected": mean_rejected,
        # Положительный разрыв — модель согласна с человеком; около нуля или
        # отрицательный — оценка не отражает того, что человек считает важным.
        "gap": gap,
        "reliable": len(accepted) >= 3 and len(rejected) >= 3,
    }


def bounds_bias(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Систематическая ошибка определения границ (§36, §63).

    Если человек раз за разом двигает начало в одну сторону, дело не
    в отдельных клипах, а в том, как стадия ищет завязку.
    """
    starts = [r["bounds_shift_start"] for r in rows if isinstance(r.get("bounds_shift_start"), (int, float))]
    ends = [r["bounds_shift_end"] for r in rows if isinstance(r.get("bounds_shift_end"), (int, float))]

    def average(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 2) if values else None

    return {
        "sample": max(len(starts), len(ends)),
        "mean_start_shift": average(starts),
        "mean_end_shift": average(ends),
        "reliable": max(len(starts), len(ends)) >= MIN_SAMPLE,
    }


def report(rows: list[dict[str, Any]], metric: str = "views") -> dict[str, Any]:
    return {
        "clips": len(rows),
        "published": sum(1 for r in rows if r.get("published_at")),
        "measured": sum(1 for r in rows if r.get("measured_at")),
        "correlations": factor_correlations(rows, metric),
        "verdicts": verdict_split(rows),
        "bounds": bounds_bias(rows),
    }
