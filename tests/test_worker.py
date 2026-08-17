"""Рабочий поток: что он записывает об итоге задачи.

Учёт должен быть честным — по нему считается нагрузка, а позже и деньги.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from narezka.api import jobs, worker
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
    status, error, seconds = worker.execute(task, config, device)
    assert status == "failed"
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
    status, error, _ = worker.execute(task, config, device)
    assert status == "finished" and error is None


def test_missing_record_is_reported_not_crashed(task_and_config) -> None:
    """Запись могли удалить, пока задача стояла в очереди."""
    task, config, device = task_and_config
    gone = queue.Task(
        task_id="t2", workspace="ivan", video_id="нет-такой", work_group="analysis",
        stage=None, force=False, status="running", created_at="2026-08-17T00:00:00+00:00",
    )
    status, error, _ = worker.execute(gone, config, device)
    assert status == "failed" and "не найдена" in error


def test_stop_marks_the_task_stopped(task_and_config, monkeypatch) -> None:
    task, config, device = task_and_config
    job, _ = jobs.manager.reserve(task.video_id, task.workspace)
    job.request_stop()
    monkeypatch.setattr(worker, "run_pipeline", lambda stages, ctx, **kw: [])
    status, _, _ = worker.execute(task, config, device)
    assert status == "stopped"
