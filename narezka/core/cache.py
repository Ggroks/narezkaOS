"""Кэш стадий по ключу.

BAZA.md §58. Ключ = хэш(имя стадии + версия кода + срез конфигурации +
отпечатки входных артефактов). Совпал и все выходы на месте — стадия
пропускается.

Именно это делает разработку на слабой машине возможной: транскрипция
фикстуры считается один раз, а не при каждой правке промпта.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from narezka.core.artifacts import Artifact
from narezka.core.stage import Stage, StageContext

STATE_VERSION = 1


@dataclass(frozen=True)
class CacheState:
    key: str
    finished_at: str
    duration_seconds: float
    outputs: list[str]


def compute_key(stage: Stage, ctx: StageContext) -> str:
    payload: dict[str, Any] = {
        "stage": stage.name,
        "stage_version": stage.version,
        "config": stage.config_slice(ctx),
        "inputs": {
            artifact.name: artifact.fingerprint() for artifact in sorted(stage.inputs(ctx), key=lambda a: a.name)
        },
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _state_artifact(stage: Stage, ctx: StageContext) -> Artifact:
    return Artifact(ctx.paths.stage_state / f"{stage.name}.json")


def load_state(stage: Stage, ctx: StageContext) -> CacheState | None:
    artifact = _state_artifact(stage, ctx)
    if not artifact.exists():
        return None
    try:
        data = artifact.read_json()
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("state_version") != STATE_VERSION:
        return None
    return CacheState(
        key=data["key"],
        finished_at=data["finished_at"],
        duration_seconds=data.get("duration_seconds", 0.0),
        outputs=data.get("outputs", []),
    )


def save_state(stage: Stage, ctx: StageContext, key: str, finished_at: str, duration: float) -> None:
    _state_artifact(stage, ctx).write_json(
        {
            "state_version": STATE_VERSION,
            "stage": stage.name,
            "stage_version": stage.version,
            "key": key,
            "finished_at": finished_at,
            "duration_seconds": round(duration, 3),
            "outputs": [str(a.path) for a in stage.outputs(ctx)],
        }
    )


def is_fresh(stage: Stage, ctx: StageContext, key: str) -> bool:
    """Кэш действителен, только если ключ совпал И все выходы физически на месте.

    Проверка выходов обязательна: файл могли удалить вручную или он мог
    не дописаться до сбоя.
    """
    state = load_state(stage, ctx)
    if state is None or state.key != key:
        return False
    return all(artifact.exists() for artifact in stage.outputs(ctx))


def invalidate(stage: Stage, ctx: StageContext) -> None:
    _state_artifact(stage, ctx).path.unlink(missing_ok=True)
