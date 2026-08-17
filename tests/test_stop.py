"""Остановка обработки без убийства сервера.

Раньше остановить прогон можно было только убийством процесса — и это
однажды снесло заодно веб-сервер, после чего интерфейс перестал открываться.
"""

import threading

from narezka.api import jobs
from narezka.core.runner import run_pipeline


def test_stop_marks_job_and_reports():
    """Просьба остановиться доходит до задачи и попадает в события."""
    manager = jobs.JobManager()
    release = threading.Event()
    started = threading.Event()

    def work(emit):
        started.set()
        release.wait(timeout=5)

    job, created = manager.start("v1", "default", work)
    assert created and started.wait(timeout=5)

    assert manager.stop("v1", "default") is True
    assert job.stop_requested
    release.set()


def test_two_people_with_one_url_get_separate_jobs():
    """Идентификатор записи выводится из ссылки: у двоих он совпадёт.

    Разойтись задачи обязаны владельцем, иначе события одного текут
    в журнал другому, а остановка одного останавливает работу обоим.
    """
    manager = jobs.JobManager()
    first, _ = manager.start("одна-и-та-же", "ivan", lambda emit: None)
    second, _ = manager.start("одна-и-та-же", "petr", lambda emit: None)
    assert first is not second
    assert manager.get("одна-и-та-же", "ivan") is first
    assert manager.get("одна-и-та-же", "petr") is second


def test_stop_without_running_job():
    """Останавливать нечего — это не ошибка, а честный ответ."""
    assert jobs.JobManager().stop("нет-такого", "default") is False


def test_pipeline_stops_between_stages():
    """Прогон прерывается на границе стадий, а не посреди работы."""
    calls: list[str] = []

    class FakeStage:
        def __init__(self, name):
            self.name = name

    stop_after_first = [False]

    def fake_run(stage, ctx, *, force, observer):
        calls.append(stage.name)
        stop_after_first[0] = True
        from narezka.core.runner import Outcome, StageResult

        return StageResult(stage=stage.name, outcome=Outcome.DONE)

    import narezka.core.runner as runner

    original = runner.run_stage
    runner.run_stage = fake_run
    try:
        results = run_pipeline(
            [FakeStage("первая"), FakeStage("вторая")], ctx=None,
            should_stop=lambda: stop_after_first[0],
        )
    finally:
        runner.run_stage = original

    assert calls == ["первая"], "вторая стадия не должна была начаться"
    assert len(results) == 1, "сделанное сохраняется, а не отбрасывается"
