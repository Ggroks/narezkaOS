"""Фоновые задачи обработки.

BAZA.md §30: HTTP-запрос не блокируется на время обработки. §69: состояние
живёт на сервере, а не в памяти вкладки — перезагрузка страницы и возврат
назавтра показывают ту же картину.

Пока это одна очередь в рамках процесса. Полноценные очереди CPU/GPU (§64)
появятся, когда обработка станет параллельной.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class JobEvent:
    seq: int
    at: str
    stage: str
    event: str
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "at": self.at, "stage": self.stage, "event": self.event, **self.payload}


@dataclass
class Job:
    video_id: str
    project_id: str
    #: queued → running → finished | failed
    status: str = "queued"
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    error: str | None = None
    #: История событий держится целиком: клиент, подключившийся позже,
    #: должен увидеть, что уже произошло (§69, состояние переживает перезагрузку).
    events: deque[JobEvent] = field(default_factory=lambda: deque(maxlen=1000))
    #: Просьба остановиться. Проверяется между стадиями: обрывать стадию
    #: посреди работы значит оставить артефакт недописанным, а незавершённая
    #: стадия всё равно будет пересчитана заново.
    _stop: threading.Event = field(default_factory=threading.Event)
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _updated: threading.Condition | None = None

    def request_stop(self) -> None:
        """Попросить остановиться после текущей стадии."""
        self._stop.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def __post_init__(self) -> None:
        self._updated = threading.Condition(self._lock)

    def add_event(self, stage: str, event: str, payload: dict[str, Any]) -> None:
        assert self._updated is not None
        with self._updated:
            self._seq += 1
            self.events.append(JobEvent(self._seq, _now(), stage, event, payload))
            self._updated.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "video_id": self.video_id,
                "project_id": self.project_id,
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
                "events": [e.as_dict() for e in self.events],
            }

    def progress(self) -> dict[str, Any]:
        """Что идёт прямо сейчас — коротко, без всей истории событий.

        Каталог проектов опрашивается раз в несколько секунд, и слать ему
        тысячу событий на каждую карточку, чтобы он взял из них последнее,
        значит гонять мегабайты ради трёх чисел.
        """
        with self._lock:
            stage = next(
                (e.stage for e in reversed(self.events) if e.event == "started"), None
            )
            step = next(
                (
                    e
                    for e in reversed(self.events)
                    if e.event == "progress" and e.stage == stage
                ),
                None,
            )
        return {
            "status": self.status,
            "stage": stage,
            "done": step.payload.get("done") if step else None,
            "total": step.payload.get("total") if step else None,
            "note": step.payload.get("note") if step else None,
            "error": self.error,
        }

    def events_since(self, seq: int, *, timeout: float = 20.0) -> list[JobEvent]:
        """Возвращает новые события, ожидая появления до timeout секунд.

        Ожидание вместо опроса: интерфейс должен показывать прогресс сразу,
        а не с задержкой в несколько секунд.
        """
        assert self._updated is not None
        with self._updated:
            if not any(e.seq > seq for e in self.events):
                self._updated.wait(timeout)
            return [e for e in self.events if e.seq > seq]

    @property
    def is_active(self) -> bool:
        return self.status in ("queued", "running")

    def cancel(self) -> None:
        """Закрывает задачу, которая так и не начала выполняться.

        Снятая из очереди задача обязана закрыться и в памяти: иначе она
        вечно числится живой — «остановить» срабатывает снова и снова,
        а поток событий ждёт того, чего не будет.
        """
        if self.status != "queued":
            return
        self.status = "stopped"
        self.finished_at = _now()
        self.add_event("*", "job_finished", {"status": self.status})

    def execute(self, work: Any) -> tuple[str, str | None]:
        """Выполняет работу, ведя состояние и события. Возвращает итог.

        Вызывается рабочим потоком, а не запросом: задача сначала стоит
        в очереди и только потом начинает выполняться. Разделение нужно,
        чтобы человек видел «третий в очереди», а не «идёт обработка»,
        когда на деле не идёт ничего.
        """
        self.status = "running"
        self.started_at = _now()
        try:
            work(self.add_event)
            self.status = "stopped" if self.stop_requested else "finished"
            error = None
        except Exception as exc:  # noqa: BLE001 — сбой задачи не должен ронять сервер
            self.status = "failed"
            self.error = error = str(exc)
            self.add_event("*", "error", {"message": str(exc)})
        finally:
            self.finished_at = _now()
            self.add_event("*", "job_finished", {"status": self.status})
        return self.status, error


class JobManager:
    """Одна активная задача на запись. Повторный запуск возвращает текущую.

    **Ключ — пара «владелец и запись», а не одна запись.** Идентификатор
    записи выводится из ссылки, поэтому двое, добавивших один и тот же VOD,
    получали одну задачу на двоих: события одного текли в журнал другому,
    а остановка одного останавливала работу обоим.
    """

    def __init__(self) -> None:
        self._jobs: dict[tuple[str, str], Job] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(video_id: str, project_id: str) -> tuple[str, str]:
        return (project_id, video_id)

    def stop(self, video_id: str, project_id: str) -> bool:
        """Просит задачу остановиться. False — останавливать нечего."""
        job = self.get(video_id, project_id)
        if job is None or not job.is_active:
            return False
        job.request_stop()
        job.add_event("*", "stop_requested", {})
        return True

    def get(self, video_id: str, project_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(self._key(video_id, project_id))

    def all(self) -> dict[tuple[str, str], Job]:
        with self._lock:
            return dict(self._jobs)

    def reserve(self, video_id: str, project_id: str) -> tuple[Job, bool]:
        """Заводит задачу в состоянии «в очереди», не запуская работу.

        Живая задача возвращается как есть: повторное нажатие не должно
        ни плодить вторую, ни стирать журнал первой.
        """
        with self._lock:
            key = self._key(video_id, project_id)
            existing = self._jobs.get(key)
            if existing is not None and existing.is_active:
                return existing, False
            job = Job(video_id=video_id, project_id=project_id)
            self._jobs[key] = job
            return job, True

    def start(self, video_id: str, project_id: str, work: Any) -> tuple[Job, bool]:
        """Запускает работу в фоне. Второй элемент — была ли задача создана.

        `work` принимает функцию-наблюдателя и выполняет обработку.
        """
        with self._lock:
            key = self._key(video_id, project_id)
            existing = self._jobs.get(key)
            if existing is not None and existing.is_active:
                return existing, False
            job = Job(video_id=video_id, project_id=project_id)
            self._jobs[key] = job

        def runner() -> None:
            job.status = "running"
            try:
                work(job.add_event)
                job.status = "stopped" if job.stop_requested else "finished"
            except Exception as exc:  # noqa: BLE001 — сбой задачи не должен ронять сервер
                job.status = "failed"
                job.error = str(exc)
                job.add_event("*", "error", {"message": str(exc)})
            finally:
                job.finished_at = _now()
                job.add_event("*", "job_finished", {"status": job.status})

        threading.Thread(target=runner, name=f"narezka-job-{video_id}", daemon=True).start()
        return job, True


#: Один менеджер на процесс сервера.
manager = JobManager()
