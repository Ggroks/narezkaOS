"""Прогресс долгих стадий.

На пятичасовой записи отбор молчал 43 минуты, а транскрипция — 77. Понять,
идёт ли работа, было нечем, кроме журнала в терминале.
"""

import logging

from narezka.core import llm
from narezka.core.stage import StageContext

LOG = logging.getLogger("test")


def _ctx(**kw) -> StageContext:
    return StageContext(
        project_id="p", video_id="v", paths=None, config=None, device=None, log=LOG, **kw
    )


def test_progress_is_silent_without_observer():
    """Запуск из CLI без наблюдателя не должен падать на вызове прогресса."""
    _ctx().progress(1, 10, "шаг")  # не бросает


def test_progress_reaches_observer():
    """С наблюдателем отметки доходят целиком, включая пояснение."""
    seen: list[tuple[int, int, str]] = []
    ctx = _ctx(on_progress=lambda d, t, n: seen.append((d, t, n)))

    ctx.progress(3, 10, "расшифровка")

    assert seen == [(3, 10, "расшифровка")]


def test_batches_report_progress(monkeypatch):
    """Пакетная обработка отмечает продвижение по мере ответов."""
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    seen: list[str] = []

    llm.process_batches(
        [1, 2], lambda b: ({b: "ok"}, "модель"), LOG,
        on_progress=lambda done, _t, note: seen.append(note),
    )

    assert seen == ["пакет 1 из 2", "пакет 2 из 2"]
