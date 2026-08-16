"""Запуск стадий: проверка применимости, кэш, учёт времени.

BAZA.md §58, §62, §67.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum

from narezka.core import cache
from narezka.core.artifacts import Artifact
from narezka.core.logging import log_context
from narezka.core.stage import Stage, StageContext, StageSkipped


class Outcome(StrEnum):
    DONE = "done"
    CACHED = "cached"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class StageResult:
    stage: str
    outcome: Outcome
    duration: float = 0.0
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is not Outcome.FAILED


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


#: Наблюдатель за ходом выполнения. Нужен интерфейсу (§69): обработка идёт
#: минутами и часами, и опрос статуса раз в несколько секунд выглядит
#: как зависшая система.
Observer = Callable[[str, str, dict], None]


def _notify(observer: Observer | None, stage: str, event: str, **payload: object) -> None:
    if observer is None:
        return
    try:
        observer(stage, event, dict(payload))
    except Exception:  # noqa: BLE001 — сбой наблюдателя не должен ронять обработку
        pass


def run_stage(
    stage: Stage,
    ctx: StageContext,
    *,
    force: bool = False,
    observer: Observer | None = None,
) -> StageResult:
    """Запускает стадию и сообщает наблюдателю о начале и об итоге."""
    _notify(observer, stage.name, "started")
    if observer is not None:
        ctx = replace(
            ctx,
            on_progress=lambda done, total, note: _notify(
                observer, stage.name, "progress", done=done, total=total, note=note
            ),
        )
    result = _run_stage_inner(stage, ctx, force=force)
    _notify(
        observer,
        stage.name,
        "finished",
        outcome=result.outcome.value,
        duration=round(result.duration, 2),
        reason=result.reason,
    )
    return result


def _run_stage_inner(stage: Stage, ctx: StageContext, *, force: bool) -> StageResult:
    with log_context(video_id=ctx.video_id, stage=stage.name):
        reason = stage.check_available(ctx)
        if reason:
            if stage.optional and ctx.config.degrade_gracefully:
                ctx.log.warning("пропущена: %s", reason)
                return StageResult(stage.name, Outcome.SKIPPED, reason=reason)
            ctx.log.error("невыполнима: %s", reason)
            return StageResult(stage.name, Outcome.FAILED, reason=reason)

        missing = [a.path.name for a in stage.inputs(ctx) if not a.exists()]
        if missing:
            reason = f"нет входных артефактов: {', '.join(missing)}"
            ctx.log.error(reason)
            return StageResult(stage.name, Outcome.FAILED, reason=reason)

        key = cache.compute_key(stage, ctx)
        if not force and cache.is_fresh(stage, ctx, key):
            ctx.log.info("из кэша")
            return StageResult(stage.name, Outcome.CACHED)

        ctx.paths.ensure()
        started = time.monotonic()
        try:
            stage.run(ctx)
        except StageSkipped as exc:
            ctx.log.warning("пропущена: %s", exc)
            return StageResult(stage.name, Outcome.SKIPPED, reason=str(exc))
        except Exception as exc:  # noqa: BLE001 — сбой одной стадии не должен ронять процесс
            ctx.log.exception("ошибка: %s", exc)
            return StageResult(stage.name, Outcome.FAILED, duration=time.monotonic() - started, reason=str(exc))

        duration = time.monotonic() - started

        not_written = [a.path.name for a in stage.outputs(ctx) if not a.exists()]
        if not_written:
            reason = f"стадия завершилась, но не создала: {', '.join(not_written)}"
            ctx.log.error(reason)
            return StageResult(stage.name, Outcome.FAILED, duration=duration, reason=reason)

        cache.save_state(stage, ctx, key, _now(), duration)
        _record_cost(ctx, stage.name, duration)
        ctx.log.info("готово за %.1f с", duration)
        return StageResult(stage.name, Outcome.DONE, duration=duration)


def run_pipeline(
    stages: list[Stage],
    ctx: StageContext,
    *,
    force: bool = False,
    observer: Observer | None = None,
    should_stop: Any = None,
) -> list[StageResult]:
    """Последовательный прогон. Останавливается на первой упавшей обязательной стадии.

    `should_stop` спрашивается между стадиями: прервать стадию посреди работы
    значит оставить артефакт недописанным, а результат всё равно потерять —
    незавершённая стадия не попадает в кэш и считается заново. Остановка на
    границе сохраняет всё, что уже сделано, и прогон продолжается с этого места.
    """
    results: list[StageResult] = []
    _notify(observer, "*", "pipeline_started", stages=[s.name for s in stages])
    for stage in stages:
        if should_stop is not None and should_stop():
            _notify(observer, "*", "pipeline_stopped", done=[r.stage for r in results])
            return results
        result = run_stage(stage, ctx, force=force, observer=observer)
        results.append(result)
        if result.outcome is Outcome.FAILED:
            ctx.log.error("pipeline остановлен на стадии %s", stage.name)
            break
    _notify(
        observer,
        "*",
        "pipeline_finished",
        failed=any(not r.ok for r in results),
    )
    return results


def _record_cost(ctx: StageContext, stage_name: str, duration: float) -> None:
    """Накопление времени по стадиям в meta/cost.json (§67)."""
    artifact = Artifact(ctx.paths.cost)
    data: dict = {"stages": {}, "device": ctx.device.kind, "profile": ctx.config.resolved_profile}
    if artifact.exists():
        try:
            data = artifact.read_json()
            data.setdefault("stages", {})
        except (ValueError, OSError):
            pass

    entry = data["stages"].setdefault(stage_name, {"runs": 0, "seconds_total": 0.0})
    entry["runs"] += 1
    entry["seconds_total"] = round(entry["seconds_total"] + duration, 3)
    entry["seconds_last"] = round(duration, 3)
    entry["device"] = ctx.device.kind
    data["updated_at"] = _now()
    artifact.write_json(data)
