"""Очередь обработки (§9A, §64).

Проверяются свойства, ради которых очередь и появилась: предел
одновременной работы, справедливость между владельцами, живучесть при
перезапуске и учёт наработанного.
"""

from __future__ import annotations

from pathlib import Path

from narezka.core import db, queue


def put(connection, workspace: str, video_id: str, **kwargs):
    task, created = queue.enqueue(
        connection, workspace=workspace, video_id=video_id, **kwargs
    )
    return task, created


def test_one_task_at_a_time_by_default(tmp_path: Path) -> None:
    """Предел параллельности — условие работы, а не оптимизация: две
    расшифровки рядом складывают расход памяти, а она уже кончалась."""
    with db.connect(tmp_path) as connection:
        put(connection, "ivan", "a")
        put(connection, "petr", "b")

        assert queue.claim(connection, parallel=1) is not None
        assert queue.claim(connection, parallel=1) is None, "взялась вторая при пределе в одну"


def test_one_person_does_not_take_the_whole_machine(tmp_path: Path) -> None:
    """У одного владельца одновременно выполняется не больше одной задачи.

    Без этого человек с десятью записями занимает машину на полсуток,
    и остальные не видят ни одного результата.
    """
    with db.connect(tmp_path) as connection:
        put(connection, "ivan", "a")
        put(connection, "ivan", "b")
        put(connection, "petr", "c")

        first = queue.claim(connection, parallel=2)
        second = queue.claim(connection, parallel=2)
        assert first.workspace == "ivan"
        assert second.workspace == "petr", "второй взялась задача того же человека"


def test_order_is_first_come(tmp_path: Path) -> None:
    with db.connect(tmp_path) as connection:
        first, _ = put(connection, "ivan", "a")
        put(connection, "ivan", "b")
        assert queue.claim(connection, parallel=1).task_id == first.task_id


def test_second_press_returns_the_same_task(tmp_path: Path) -> None:
    """Двойной клик не должен ставить две сборки подряд."""
    with db.connect(tmp_path) as connection:
        first, created_first = put(connection, "ivan", "a")
        second, created_second = put(connection, "ivan", "a")
        assert created_first and not created_second
        assert first.task_id == second.task_id


def test_position_counts_from_one(tmp_path: Path) -> None:
    with db.connect(tmp_path) as connection:
        first, _ = put(connection, "ivan", "a")
        second, _ = put(connection, "petr", "b")
        assert queue.position(connection, first.task_id) == 1
        assert queue.position(connection, second.task_id) == 2

        queue.claim(connection, parallel=1)
        # Взятая в работу больше не «в очереди»: место у неё нулевое.
        assert queue.position(connection, first.task_id) == 0
        assert queue.position(connection, second.task_id) == 1


def test_restart_returns_the_work_to_the_queue(tmp_path: Path) -> None:
    """Задача, оставшаяся «выполняющейся» после падения, — это след смерти
    процесса, который её вёл. Повтор стоит дёшево: стадии кэшируются."""
    with db.connect(tmp_path) as connection:
        put(connection, "ivan", "a")
        taken = queue.claim(connection, parallel=1)
        assert taken is not None

        assert queue.requeue_orphans(connection) == 1
        again = queue.claim(connection, parallel=1)
        assert again is not None and again.task_id == taken.task_id


def test_cancel_only_touches_what_has_not_started(tmp_path: Path) -> None:
    """Начатую задачу снимает сам прогон — на границе стадий, чтобы
    не оставить недописанный артефакт."""
    with db.connect(tmp_path) as connection:
        waiting, _ = put(connection, "ivan", "a")
        assert queue.cancel(connection, waiting.task_id) is True

        put(connection, "petr", "b")
        started = queue.claim(connection, parallel=1)
        assert queue.cancel(connection, started.task_id) is False


def test_finished_task_frees_the_place(tmp_path: Path) -> None:
    with db.connect(tmp_path) as connection:
        put(connection, "ivan", "a")
        put(connection, "petr", "b")
        first = queue.claim(connection, parallel=1)
        queue.finish(connection, first.task_id, status="finished", seconds=12.0)

        second = queue.claim(connection, parallel=1)
        assert second is not None and second.workspace == "petr"


def test_usage_counts_hours_of_video(tmp_path: Path) -> None:
    """Часы записи — то, по чему считается стоимость: себестоимость линейна
    по ним и ни по чему другому. Машинные секунды — чтобы видеть, во что
    это обходится на самом деле."""
    with db.connect(tmp_path) as connection:
        for name in ("a", "b"):
            task, _ = put(connection, "ivan", name)
            queue.claim(connection, parallel=1)
            queue.finish(
                connection, task.task_id, status="finished",
                seconds=1800, video_seconds=3600 * 2,
            )
        task, _ = put(connection, "petr", "c")
        queue.claim(connection, parallel=1)
        queue.finish(connection, task.task_id, status="finished", seconds=600, video_seconds=1800)

        report = {row["workspace"]: row for row in queue.usage(connection)}
        assert report["ivan"]["video_hours"] == 4.0
        assert report["ivan"]["machine_seconds"] == 3600.0
        assert report["petr"]["video_hours"] == 0.5


def test_failed_task_still_counts_as_work(tmp_path: Path) -> None:
    """Упавшая задача успела съесть машинное время, и в учёт оно обязано
    попасть: иначе нагрузка выглядит меньше, чем есть."""
    with db.connect(tmp_path) as connection:
        task, _ = put(connection, "ivan", "a")
        queue.claim(connection, parallel=1)
        queue.finish(
            connection, task.task_id, status="failed",
            error="кончилось место", seconds=90, video_seconds=600,
        )
        assert queue.usage(connection)[0]["machine_seconds"] == 90.0
