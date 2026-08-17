"""Рабочие потоки: берут задачи из очереди и выполняют.

Отдельный слой между очередью и стадиями. Пока это потоки внутри того же
процесса, что и веб: полноценные рабочие на отдельных машинах — 9B, и
переезд к ним не должен требовать переписывания стадий. Поэтому здесь нет
ничего, кроме «взять задачу, собрать окружение, выполнить, записать итог».

**Почему прерванная задача возобновляется дёшево.** Стадии кэшируются
по ключу от входов и настроек, поэтому повтор прогона пропускает всё, что
успело завершиться. Именно это делает очередь, переживающую перезапуск,
осмысленной: возвращать задачу в очередь не страшно.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from narezka.api import jobs
from narezka.core import credits, db, queue
from narezka.core.artifacts import Artifact
from narezka.core.logging import get_logger
from narezka.core.paths import video_paths
from narezka.core.runner import Outcome, run_pipeline, run_stage
from narezka.core.stage import StageContext
from narezka.stages import PIPELINE, REGISTRY, get_stage, stages_for

log = get_logger("worker")

#: Как часто заглядывать в пустую очередь. Секунда: человек, нажавший
#: «собрать», не должен ждать заметно дольше самой обработки.
IDLE_SLEEP = 1.0


def execute(task: queue.Task, config, device) -> tuple[str, str | None, float, set[str]]:
    """Выполняет одну задачу.

    Возвращает итог, причину, потраченное время и куски работы, которые
    действительно выполнялись. Последнее — для оплаты: взятое из кэша не
    стоит ничего, иначе повторное нажатие штрафовало бы за осторожность.
    """
    paths = video_paths(config.storage_root, task.workspace, task.video_id)
    if not paths.exists():
        return "failed", "запись не найдена", 0.0, set()

    ctx = StageContext(
        project_id=task.workspace,
        video_id=task.video_id,
        paths=paths,
        config=config,
        device=device,
        log=log,
    )

    job, _ = jobs.manager.reserve(task.video_id, task.workspace)
    started = time.monotonic()
    #: Упавшие стадии. Прогон не бросает исключение — он останавливается
    #: и возвращает итоги, поэтому без этого списка задача записывалась бы
    #: «выполненной» даже когда ни один ролик не собрался.
    trouble: list[str] = []
    #: Куски работы, где хоть одна стадия отработала заново. Только они
    #: и оплачиваются.
    executed: set[str] = set()

    def work(emit) -> None:
        observer = lambda name, event, data: emit(name, event, data)  # noqa: E731
        if task.stage:
            results = [run_stage(get_stage(task.stage), ctx, force=task.force, observer=observer)]
        else:
            planned = stages_for(task.work_group) if task.work_group else list(PIPELINE)
            results = run_pipeline(
                planned, ctx, force=task.force, observer=observer,
                should_stop=lambda: job.stop_requested,
            )
        trouble.extend(
            f"{item.stage}: {item.reason or 'ошибка'}" for item in results if not item.ok
        )
        executed.update(
            REGISTRY[item.stage].group
            for item in results
            if item.outcome is Outcome.DONE and item.stage in REGISTRY
        )

    status, error = job.execute(work)
    if status == "finished" and trouble:
        # Учёт должен быть честным: по этим записям считается нагрузка
        # и деньги.
        status, error = "failed", "; ".join(trouble)
    return status, error, time.monotonic() - started, executed


def video_seconds(config, task: queue.Task) -> float:
    """Длина записи — то, по чему считается стоимость работы."""
    paths = video_paths(config.storage_root, task.workspace, task.video_id)
    meta = Artifact(paths.metadata)
    if not meta.exists():
        return 0.0
    try:
        return float(meta.read_json().get("duration_seconds") or 0.0)
    except (ValueError, TypeError):
        return 0.0


class Worker(threading.Thread):
    """Один разбирающий поток. Их столько, сколько разрешено параллельности."""

    def __init__(self, config_provider: Any, parallel: int, name: str) -> None:
        super().__init__(name=name, daemon=True)
        self._config_provider = config_provider
        self._parallel = parallel
        self._stop = threading.Event()

    def shutdown(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001 — рабочий поток не должен умирать
                log.exception("сбой рабочего потока: %s", exc)
                self._stop.wait(IDLE_SLEEP)

    def _tick(self) -> None:
        config, device = self._config_provider()
        with db.connect(config.storage_root) as connection:
            task = queue.claim(connection, parallel=self._parallel)
        if task is None:
            self._stop.wait(IDLE_SLEEP)
            return

        log.info(
            "задача %s: %s/%s (%s)",
            task.task_id[:8], task.workspace, task.video_id,
            task.stage or task.work_group or "всё",
        )
        status, error, seconds, executed = execute(task, config, device)
        length = video_seconds(config, task)

        with db.connect(config.storage_root) as connection:
            queue.finish(
                connection, task.task_id,
                status=status, error=error,
                seconds=seconds, video_seconds=length,
            )
            charged = self._charge(connection, config, task, status, executed, length)

        log.info(
            "задача %s: %s за %.1f с%s",
            task.task_id[:8], status, seconds,
            f", списано {charged:.0f}" if charged else "",
        )

    @staticmethod
    def _charge(connection, config, task, status: str, executed: set[str], length: float) -> float:
        """Списывает за выполненную работу.

        Неудача и остановка не оплачиваются: человек не получил результата,
        а цена ошибки сервиса не должна ложиться на того, кто её не совершал.
        Взятое из кэша тоже бесплатно — иначе повторное нажатие штрафовало бы
        за осторожность.
        """
        if not config.billing.enabled or status != "finished" or not executed:
            return 0.0
        quote = credits.quote_for(config.billing, executed, length)
        if quote.credits <= 0:
            return 0.0
        credits.charge(
            connection, workspace=task.workspace, quote=quote,
            task_id=task.task_id, video_id=task.video_id,
        )
        return quote.credits


def start(config_provider: Any, parallel: int) -> list[Worker]:
    """Поднимает рабочие потоки и возвращает их в очередь всё, что зависло.

    Задачи в состоянии «выполняется» на старте — это следы падения:
    выполнять их некому, потому что процесс, который их вёл, умер.
    """
    config, _ = config_provider()
    with db.connect(config.storage_root) as connection:
        orphans = queue.requeue_orphans(connection)
    if orphans:
        log.info("возвращено в очередь после перезапуска: %d", orphans)

    workers = [Worker(config_provider, parallel, f"narezka-worker-{i + 1}") for i in range(parallel)]
    for worker in workers:
        worker.start()
    return workers
