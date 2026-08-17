"""Рабочий поток: что он записывает об итоге задачи.

Учёт должен быть честным — по нему считается нагрузка, а позже и деньги.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from narezka.api import jobs, worker
from narezka.core.artifacts import Artifact
from narezka.core import queue
from narezka.core.config import load_config
from narezka.core.device import DeviceInfo
from narezka.core.paths import video_paths
from narezka.core.runner import Outcome, StageResult


@pytest.fixture
def task_and_config(tmp_path: Path):
    config = load_config().model_copy(update={"storage_root": tmp_path})
    paths = video_paths(tmp_path, "ivan", "v1")
    paths.ensure()
    task = queue.Task(
        task_id="t1", workspace="ivan", video_id="v1", work_group="analysis",
        stage=None, force=False, status="running", created_at="2026-08-17T00:00:00+00:00",
    )
    return task, config, DeviceInfo(kind="cpu", name="test")


def test_failed_stage_makes_the_task_failed(task_and_config, monkeypatch) -> None:
    """Прогон не бросает исключение — он останавливается и возвращает итоги.

    Без разбора этих итогов задача записывалась бы «выполненной» даже тогда,
    когда не собралось ни одного ролика.
    """
    task, config, device = task_and_config
    monkeypatch.setattr(
        worker, "run_pipeline",
        lambda stages, ctx, **kw: [
            StageResult("download", Outcome.DONE),
            StageResult("transcribe", Outcome.FAILED, reason="кончилось место"),
        ],
    )
    status, error, seconds, executed = worker.execute(task, config, device)
    assert status == "failed"
    # За упавшую работу платить не за что — и оплачиваемых кусков нет.
    assert executed == {"analysis"}
    assert "кончилось место" in error
    assert seconds >= 0


def test_skipped_stage_is_not_a_failure(task_and_config, monkeypatch) -> None:
    """Пропуск — обычный ход работы: на записи без чата чтение чата
    пропускается, и задача от этого не становится неудачной."""
    task, config, device = task_and_config
    monkeypatch.setattr(
        worker, "run_pipeline",
        lambda stages, ctx, **kw: [
            StageResult("chat", Outcome.SKIPPED, reason="не Twitch"),
            StageResult("candidates", Outcome.DONE),
        ],
    )
    status, error, _, executed = worker.execute(task, config, device)
    assert status == "finished" and error is None
    # Пропуск не выполнялся и в оплату не идёт, а «Поиск моментов» идёт.
    assert executed == {"analysis"}


def test_missing_record_is_reported_not_crashed(task_and_config) -> None:
    """Запись могли удалить, пока задача стояла в очереди."""
    task, config, device = task_and_config
    gone = queue.Task(
        task_id="t2", workspace="ivan", video_id="нет-такой", work_group="analysis",
        stage=None, force=False, status="running", created_at="2026-08-17T00:00:00+00:00",
    )
    status, error, _, _executed = worker.execute(gone, config, device)
    assert status == "failed" and "не найдена" in error


def test_stop_marks_the_task_stopped(task_and_config, monkeypatch) -> None:
    task, config, device = task_and_config
    job, _ = jobs.manager.reserve(task.video_id, task.workspace)
    job.request_stop()
    monkeypatch.setattr(worker, "run_pipeline", lambda stages, ctx, **kw: [])
    status, _, _, _executed = worker.execute(task, config, device)
    assert status == "stopped"


# --- списание --------------------------------------------------------------


def test_finished_task_is_charged_for_what_ran(tmp_path: Path, monkeypatch) -> None:
    """Проверка проводки целиком: рабочий поток взял задачу, выполнил её
    и записал списание. Раньше это место закрывали только тесты частей,
    а связка между ними — то самое, где на этом проекте пряталась половина
    ошибок."""
    from narezka.api import worker as worker_module
    from narezka.core import credits, db, queue
    from narezka.core.config import load_config
    from narezka.core.device import DeviceInfo
    from narezka.core.paths import video_paths

    config = load_config().model_copy(update={"storage_root": tmp_path})
    config.billing.enabled = True
    config.billing.per_video_hour = {"analysis": 20.0, "shorts": 8.0}
    device = DeviceInfo(kind="cpu", name="test")

    paths = video_paths(tmp_path, "ivan", "v1")
    paths.ensure()
    Artifact(paths.metadata).write_json({"video_id": "v1", "duration_seconds": 7200})

    with db.connect(tmp_path) as connection:
        credits.add(connection, workspace="ivan", amount=100)
        queue.enqueue(connection, workspace="ivan", video_id="v1", work_group="analysis")

    monkeypatch.setattr(
        worker_module, "execute",
        lambda task, cfg, dev: ("finished", None, 3.0, {"analysis"}),
    )
    worker_module.Worker(lambda: (config, device), 1, "тест")._tick()

    with db.connect(tmp_path) as connection:
        assert credits.balance(connection, "ivan") == 60.0  # 100 − 2 ч × 20
        entry = credits.history(connection, "ivan")[0]
        assert entry["kind"] == "charge" and entry["video_hours"] == 2.0


def test_failed_task_is_not_charged(tmp_path: Path, monkeypatch) -> None:
    """Человек не получил результата: цена ошибки сервиса не должна ложиться
    на того, кто её не совершал."""
    from narezka.api import worker as worker_module
    from narezka.core import credits, db, queue
    from narezka.core.config import load_config
    from narezka.core.device import DeviceInfo
    from narezka.core.paths import video_paths

    config = load_config().model_copy(update={"storage_root": tmp_path})
    config.billing.enabled = True
    device = DeviceInfo(kind="cpu", name="test")
    video_paths(tmp_path, "ivan", "v1").ensure()

    with db.connect(tmp_path) as connection:
        credits.add(connection, workspace="ivan", amount=100)
        queue.enqueue(connection, workspace="ivan", video_id="v1", work_group="analysis")

    monkeypatch.setattr(
        worker_module, "execute",
        lambda task, cfg, dev: ("failed", "кончилось место", 3.0, {"analysis"}),
    )
    worker_module.Worker(lambda: (config, device), 1, "тест")._tick()

    with db.connect(tmp_path) as connection:
        assert credits.balance(connection, "ivan") == 100.0


def test_cached_run_costs_nothing(tmp_path: Path, monkeypatch) -> None:
    """Повторное нажатие обычно не делает ничего — брать за это деньги
    значило бы штрафовать за осторожность."""
    from narezka.api import worker as worker_module
    from narezka.core import credits, db, queue
    from narezka.core.config import load_config
    from narezka.core.device import DeviceInfo
    from narezka.core.paths import video_paths

    config = load_config().model_copy(update={"storage_root": tmp_path})
    config.billing.enabled = True
    device = DeviceInfo(kind="cpu", name="test")
    video_paths(tmp_path, "ivan", "v1").ensure()

    with db.connect(tmp_path) as connection:
        credits.add(connection, workspace="ivan", amount=100)
        queue.enqueue(connection, workspace="ivan", video_id="v1", work_group="analysis")

    # Ничего не выполнялось — всё взято из кэша.
    monkeypatch.setattr(
        worker_module, "execute", lambda task, cfg, dev: ("finished", None, 0.2, set()),
    )
    worker_module.Worker(lambda: (config, device), 1, "тест")._tick()

    with db.connect(tmp_path) as connection:
        assert credits.balance(connection, "ivan") == 100.0
