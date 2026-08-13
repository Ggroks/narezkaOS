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
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _updated: threading.Condition | None = None

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


class JobManager:
    """Одна активная задача на видео. Повторный запуск возвращает текущую."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, video_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(video_id)

    def all(self) -> dict[str, Job]:
        with self._lock:
            return dict(self._jobs)

    def start(self, video_id: str, project_id: str, work: Any) -> tuple[Job, bool]:
        """Запускает работу в фоне. Второй элемент — была ли задача создана.

        `work` принимает функцию-наблюдателя и выполняет обработку.
        """
        with self._lock:
            existing = self._jobs.get(video_id)
            if existing is not None and existing.is_active:
                return existing, False
            job = Job(video_id=video_id, project_id=project_id)
            self._jobs[video_id] = job

        def runner() -> None:
            job.status = "running"
            try:
                work(job.add_event)
                job.status = "finished"
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
