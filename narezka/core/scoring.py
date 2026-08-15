"""Расчёт итоговой оценки клипа.

BAZA.md §12 и §38. Главное правило схемы: `interest_score` — **производная**
от факторов и штрафов по весам из конфига, а не самостоятельное число.
Поэтому модель возвращает факторы, а оценку считает код: иначе веса из
конфига ни на что не влияют, а отладить, почему клип получил 0.87, нельзя.

Название `interest_score`, а не `viral_score` — намеренно (§54): это оценка
вероятности, а не обещание.
"""

from __future__ import annotations

from typing import Any

#: Факторы, которые может оценить модель, читающая только текст.
TEXT_FACTORS = ("semantic", "emotion", "context", "completeness", "novelty")

#: Фактор, считаемый из измеренных сигналов, а не спрашиваемый у модели:
#: выразительность звучания слышна в громкости, а модель читает текст.
#: На первом прогоне она честно ставила сюда ноль всем клипам подряд.
MEASURED_FACTOR = "audio"

#: Фактор, который по транскрипту оценить нельзя. Спрашивать его у текстовой
#: модели значит получить выдуманное число, неотличимое от измеренного, —
#: ровно тот класс самообмана, от которого предостерегает §54. Появится
#: на этапе 4 вместе с компьютерным зрением.
VISUAL_FACTOR = "visual"

#: Штраф, который виден в тексте: обрыв без развязки (§15).
TEXT_PENALTIES = ("unresolved_ending",)

#: Штраф, требующий разбора звука (§10, §59). Модель его не слышит, а её
#: «0.0» неотличимо от проверенного отсутствия музыки — и потому опаснее
#: пропуска. Появится вместе с аудио-тегами на этапе 3.
UNMEASURED_PENALTIES = ("music_present",)

PENALTIES = (*TEXT_PENALTIES, *UNMEASURED_PENALTIES)

#: Нормировка измеренной громкости в шкалу 0–1. Робастная z-оценка выше
#: трёх — это уже выраженный всплеск на фоне остальной записи (§11).
LOUDNESS_Z_FULL_SCALE = 3.0

#: Насколько сильно штраф давит на итог. Штраф 1.0 при этом множителе
#: срезает оценку вдвое, а не обнуляет: музыка делает клип хуже, но не
#: превращает его в мусор.
PENALTY_WEIGHT = 0.5


def normalized_weights(weights: dict[str, float], available: list[str]) -> dict[str, float]:
    """Веса, пересчитанные на те факторы, которые реально измерены.

    Если фактор не измерен (визуальный до этапа 4), его вес не пропадает,
    а распределяется между остальными. Иначе клипы получали бы заниженную
    оценку просто потому, что часть системы ещё не написана.
    """
    present = {name: max(float(weights.get(name, 0.0)), 0.0) for name in available}
    total = sum(present.values())
    if total <= 0:
        # Веса не заданы — считаем факторы равнозначными, а не делим на ноль.
        return {name: 1.0 / len(available) for name in available} if available else {}
    return {name: value / total for name, value in present.items()}


def audio_factor(signals: dict[str, Any] | None) -> float | None:
    """Выразительность звучания по измеренной громкости.

    Берётся из сигналов, посчитанных стадией candidates: это настоящий
    замер, а не мнение модели о звуке, которого она не слышала.
    """
    if not signals:
        return None
    z = signals.get("loudness_z")
    if z is None:
        return None
    try:
        return round(clamp01(float(z) / LOUDNESS_Z_FULL_SCALE), 4)
    except (TypeError, ValueError):
        return None


def clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(number, 0.0), 1.0)


def interest_score(
    factors: dict[str, Any],
    penalties: dict[str, Any] | None,
    weights: dict[str, float],
) -> float:
    """Итоговая оценка: взвешенная сумма факторов минус штрафы."""
    measured = [name for name in factors if factors.get(name) is not None]
    if not measured:
        return 0.0

    share = normalized_weights(weights, measured)
    base = sum(clamp01(factors[name]) * share.get(name, 0.0) for name in measured)

    penalty = sum(clamp01(value) for value in (penalties or {}).values() if value is not None)
    return round(clamp01(base * (1.0 - PENALTY_WEIGHT * clamp01(penalty))), 4)


def fit_duration(
    start: float,
    end: float,
    *,
    min_duration: float,
    max_duration: float,
    limit_start: float,
    limit_end: float,
) -> tuple[float, float]:
    """Приводит уточнённые границы к допустимой длительности (§17).

    Модель уточняет границы по смыслу и об ограничениях площадки не знает:
    на записи стрима она однажды ужала клип с 62 секунд до 6, а шестисекундный
    ролик публиковать некуда. Проверять результат обязан код.

    Начало сохраняется: §14 требует смысловой завязки, и именно её модель
    выбирала осознанно. Двигается конец, и только упёршись в край кандидата —
    начало.
    """
    if limit_end - limit_start < min_duration:
        # Сам кандидат короче минимума — растягивать не из чего.
        return limit_start, limit_end

    start = min(max(start, limit_start), limit_end)
    end = min(max(end, start), limit_end)

    if end - start > max_duration:
        return start, start + max_duration

    if end - start < min_duration:
        end = start + min_duration
        if end > limit_end:
            end = limit_end
            start = max(limit_start, end - min_duration)

    return start, end


def build_clip(
    *,
    video_id: str,
    index: int,
    candidate: dict[str, Any],
    verdict: dict[str, Any],
    weights: dict[str, float],
    schema_version: int,
    model: str,
    min_duration: float = 0.0,
    max_duration: float = float("inf"),
) -> dict[str, Any]:
    """Собирает запись клипа по схеме §12.

    Границы берутся уточнённые, и оценка считается **после** уточнения:
    сдвинув границы, мы меняем содержимое клипа, а значит и его ценность.
    """
    factors = {name: clamp01(verdict.get("factors", {}).get(name)) for name in TEXT_FACTORS}
    # Звук измерен, а не спрошен: модель читает текст и не слышит записи.
    factors[MEASURED_FACTOR] = audio_factor(candidate.get("signals"))
    # Визуальный фактор существует в схеме, но честно помечен неизмеренным.
    factors[VISUAL_FACTOR] = None

    penalties: dict[str, Any] = {
        name: clamp01(verdict.get("penalties", {}).get(name)) for name in TEXT_PENALTIES
    }
    # Неизмеренное остаётся None: ноль здесь читался бы как «проверено,
    # музыки нет», хотя проверять пока нечем.
    for name in UNMEASURED_PENALTIES:
        penalties[name] = None

    start = float(verdict.get("start", candidate["start"]))
    end = float(verdict.get("end", candidate["end"]))
    if end <= start:
        start, end = float(candidate["start"]), float(candidate["end"])

    if min_duration > 0 or max_duration < float("inf"):
        start, end = fit_duration(
            start, end,
            min_duration=min_duration,
            max_duration=max_duration,
            limit_start=float(candidate["start"]),
            limit_end=float(candidate["end"]),
        )

    return {
        "clip_id": f"{video_id}_c{index:02d}",
        "source_video_id": video_id,
        "index": index,
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(end - start, 2),
        "score_schema_version": schema_version,
        "interest_score": interest_score(factors, penalties, weights),
        "factors": factors,
        "penalties": penalties,
        # Сырые сигналы, породившие кандидата, — для последующего анализа (§12).
        "signals": candidate.get("signals", {}),
        "clip_type": verdict.get("clip_type") or "unknown",
        "explanation": (verdict.get("explanation") or "").strip(),
        "original": {"start": candidate["start"], "end": candidate["end"]},
        "model": model,
    }


def select_top(clips: list[dict[str, Any]], limit: int, min_score: float = 0.0) -> list[dict[str, Any]]:
    """Лучшие клипы по оценке.

    Порядок вывода — по времени, а не по оценке: смотреть нарезку удобнее
    в порядке записи, а ранг и так записан в каждом клипе.
    """
    ranked = sorted(clips, key=lambda c: c["interest_score"], reverse=True)
    chosen = [c for c in ranked if c["interest_score"] >= min_score][:limit]
    for rank, clip in enumerate(sorted(chosen, key=lambda c: -c["interest_score"]), start=1):
        clip["rank"] = rank
    return sorted(chosen, key=lambda c: c["start"])
