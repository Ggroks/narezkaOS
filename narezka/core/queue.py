"""Очередь обработки.

Раньше задача запускалась прямо из HTTP-запроса и жила в памяти процесса.
Для инструмента на одного этого хватало, для сервиса — нет, по трём причинам,
каждая из которых уже случалась на этом проекте.

1. **Память кончается.** Одна расшифровка занимает машину на час с лишним;
   две одновременные складывают расход, а память на этой машине кончалась
   трижды. Предел параллельности — не оптимизация, а условие работы.
2. **Перезапуск сервера терял всё.** Очередь в памяти исчезает вместе
   с процессом, и человек, поставивший запись на ночь, утром не находит
   ничего. В базе она переживает перезапуск, а прерванная работа
   возобновляется почти бесплатно: стадии кэшируются, и пересчитывается
   только та, что не успела завершиться.
3. **Один человек занимал бы машину целиком.** Поэтому у каждого владельца
   одновременно выполняется не больше одной задачи: остальные ждут своей
   очереди, даже если машина свободна и в очереди только он. Это стоит
   ему нескольких минут, а всем остальным — возможности вообще работать.

**Учёт.** Каждая завершённая задача пишет, сколько машинного времени она
съела и какой длины была запись. Первое нужно, чтобы понимать нагрузку,
второе — чтобы считать тарифы: себестоимость линейна по часам записи
и ни по чему другому.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

#: Сколько задач выполняется одновременно. Единица по замерам, а не из
#: осторожности: расшифровка на шести ядрах занимает их все, и вторая
#: рядом не ускоряет обработку, а замедляет обе и множит расход памяти.
DEFAULT_PARALLEL = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id       TEXT PRIMARY KEY,
    workspace     TEXT NOT NULL,
    video_id      TEXT NOT NULL,
    -- Что делать: кусок работы (analysis|shorts|long), одна стадия или всё.
    work_group    TEXT,
    stage         TEXT,
    force         INTEGER NOT NULL DEFAULT 0,
    -- queued → running → finished | failed | stopped
    status        TEXT NOT NULL DEFAULT 'queued',
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    error         TEXT,
    -- Учёт: машинные секунды и длительность записи в секундах.
    seconds       REAL NOT NULL DEFAULT 0,
    video_seconds REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tasks_waiting ON tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_space ON tasks(workspace, status);
"""

ACTIVE = ("queued", "running")


@dataclass(frozen=True)
class Task:
    task_id: str
    workspace: str
    video_id: str
    work_group: str | None
    stage: str | None
    force: bool
    status: str
    created_at: str

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def _task(row: Any) -> Task:
    return Task(
        task_id=row["task_id"],
        workspace=row["workspace"],
        video_id=row["video_id"],
        work_group=row["work_group"],
        stage=row["stage"],
        force=bool(row["force"]),
        status=row["status"],
        created_at=row["created_at"],
    )


def enqueue(
    connection: sqlite3.Connection,
    *,
    workspace: str,
    video_id: str,
    work_group: str | None = None,
    stage: str | None = None,
    force: bool = False,
) -> tuple[Task, bool]:
    """Ставит задачу в очередь. Второе значение — была ли она создана.

    Повторное нажатие на ту же запись не плодит задачи: пока прежняя жива,
    возвращается она. Иначе двойной клик по «Собрать» ставил бы две сборки
    подряд, вторая из которых не делает ничего, кроме занятия очереди.
    """
    ensure_schema(connection)
    existing = active_for(connection, workspace=workspace, video_id=video_id)
    if existing is not None:
        return existing, False

    task_id = uuid.uuid4().hex
    connection.execute(
        "INSERT INTO tasks (task_id, workspace, video_id, work_group, stage, force,"
        " status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)",
        (task_id, workspace, video_id, work_group, stage, int(force), _now()),
    )
    connection.commit()
    return _fetch(connection, task_id), True


def _fetch(connection: sqlite3.Connection, task_id: str) -> Task:
    row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise KeyError(task_id)
    return _task(row)


def active_for(connection: sqlite3.Connection, *, workspace: str, video_id: str) -> Task | None:
    """Живая задача по этой записи, если она есть."""
    ensure_schema(connection)
    row = connection.execute(
        "SELECT * FROM tasks WHERE workspace = ? AND video_id = ? AND status IN ('queued','running')"
        " ORDER BY created_at LIMIT 1",
        (workspace, video_id),
    ).fetchone()
    return _task(row) if row else None


def claim(connection: sqlite3.Connection, *, parallel: int = DEFAULT_PARALLEL) -> Task | None:
    """Берёт следующую задачу в работу или возвращает None.

    Два условия: всего выполняется меньше предела и у этого владельца
    сейчас ничего не выполняется. Второе — не справедливость ради
    справедливости: без него человек с десятью записями занимает машину
    на полсуток, и остальные не видят ни одного результата.

    Транзакция немедленная: два рабочих потока не должны взять одну задачу.
    """
    ensure_schema(connection)
    connection.execute("BEGIN IMMEDIATE")
    try:
        running = connection.execute(
            "SELECT workspace, COUNT(*) AS n FROM tasks WHERE status = 'running' GROUP BY workspace"
        ).fetchall()
        total = sum(row["n"] for row in running)
        busy = {row["workspace"] for row in running}
        if total >= parallel:
            connection.rollback()
            return None

        row = connection.execute(
            "SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at, rowid"
        ).fetchall()
        chosen = next((item for item in row if item["workspace"] not in busy), None)
        if chosen is None:
            connection.rollback()
            return None

        connection.execute(
            "UPDATE tasks SET status = 'running', started_at = ? WHERE task_id = ?",
            (_now(), chosen["task_id"]),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return _fetch(connection, chosen["task_id"])


def finish(
    connection: sqlite3.Connection,
    task_id: str,
    *,
    status: str,
    error: str | None = None,
    seconds: float = 0.0,
    video_seconds: float = 0.0,
) -> None:
    ensure_schema(connection)
    connection.execute(
        "UPDATE tasks SET status = ?, finished_at = ?, error = ?, seconds = ?, video_seconds = ?"
        " WHERE task_id = ?",
        (status, _now(), error, round(seconds, 3), round(video_seconds, 3), task_id),
    )
    connection.commit()


def cancel(connection: sqlite3.Connection, task_id: str) -> bool:
    """Снимает задачу, которая ещё не начата. Выполняемую не трогает —
    её останавливает сам прогон, на границе стадий."""
    ensure_schema(connection)
    cursor = connection.execute(
        "UPDATE tasks SET status = 'stopped', finished_at = ? WHERE task_id = ? AND status = 'queued'",
        (_now(), task_id),
    )
    connection.commit()
    return cursor.rowcount > 0


def position(connection: sqlite3.Connection, task_id: str) -> int:
    """Место в очереди. 0 — уже выполняется, 1 — следующая на очереди."""
    ensure_schema(connection)
    row = connection.execute(
        "SELECT rowid, status, created_at FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    if row is None or row["status"] != "queued":
        return 0
    # Время записано с точностью до секунды, и две задачи, поставленные
    # подряд, получают одинаковую метку: без разрешения по rowid обе
    # оказывались бы первыми в очереди.
    ahead = connection.execute(
        "SELECT COUNT(*) AS n FROM tasks WHERE status = 'queued'"
        " AND (created_at < ? OR (created_at = ? AND rowid < ?))",
        (row["created_at"], row["created_at"], row["rowid"]),
    ).fetchone()["n"]
    return int(ahead) + 1


def waiting(connection: sqlite3.Connection) -> int:
    ensure_schema(connection)
    return int(
        connection.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE status = 'queued'"
        ).fetchone()["n"]
    )


def requeue_orphans(connection: sqlite3.Connection) -> int:
    """Возвращает в очередь то, что осталось «выполняющимся» после падения.

    Задача, прерванная на середине, повторяется почти бесплатно: стадии
    кэшируются, и пересчитается только та, что не успела дописать артефакт.
    """
    ensure_schema(connection)
    cursor = connection.execute(
        "UPDATE tasks SET status = 'queued', started_at = NULL WHERE status = 'running'"
    )
    connection.commit()
    return cursor.rowcount


def usage(connection: sqlite3.Connection, *, workspace: str | None = None, since: str | None = None):
    """Сколько наработано: часы записи и машинное время.

    Часы записи — то, по чему считается стоимость: она линейна по ним
    и ни по чему другому. Машинные секунды нужны, чтобы видеть, во что это
    обходится на самом деле.
    """
    ensure_schema(connection)
    where = ["status IN ('finished','stopped','failed')"]
    params: list[Any] = []
    if workspace is not None:
        where.append("workspace = ?")
        params.append(workspace)
    if since is not None:
        where.append("finished_at >= ?")
        params.append(since)

    rows = connection.execute(
        f"SELECT workspace, COUNT(*) AS tasks, SUM(seconds) AS machine,"  # noqa: S608 — поля свои
        f" SUM(video_seconds) AS video FROM tasks WHERE {' AND '.join(where)}"
        f" GROUP BY workspace ORDER BY video DESC",
        params,
    ).fetchall()
    return [
        {
            "workspace": row["workspace"],
            "tasks": int(row["tasks"]),
            "machine_seconds": round(row["machine"] or 0.0, 1),
            "video_hours": round((row["video"] or 0.0) / 3600, 2),
        }
        for row in rows
    ]
