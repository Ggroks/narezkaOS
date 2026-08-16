"""Повтор упавших пакетов.

Замерено на пятичасовой записи: один пакет из тридцати отказал, и шесть
роликов остались без заголовков. Пропуск внутри прохода терял работу, которую
достаточно было повторить минутой позже.
"""

import logging

import pytest

from narezka.core import llm

LOG = logging.getLogger("test")


def test_temporary_failure_is_retried(monkeypatch):
    """Пакет, отказавший по занятости, доходит со второго захода."""
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    seen: list[int] = []

    def ask(batch):
        seen.append(batch)
        if batch == 2 and seen.count(2) == 1:
            raise llm.RateLimited("пул занят")
        return {batch: f"ответ {batch}"}, "модель"

    answers, models, failures = llm.process_batches([1, 2, 3], ask, LOG)

    assert answers == {1: "ответ 1", 2: "ответ 2", 3: "ответ 3"}
    assert failures == []
    assert models == {"модель"}
    # Повтор идёт после основного прохода, а не сразу: пулу нужно время.
    assert seen == [1, 2, 3, 2]


def test_broken_answer_is_not_retried(monkeypatch):
    """Неразобранный ответ — не временный отказ, ждать бессмысленно."""
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    calls: list[int] = []

    def ask(batch):
        calls.append(batch)
        raise ValueError("не разобрать ответ")

    answers, _, failures = llm.process_batches([1], ask, LOG)

    assert answers == {}
    assert calls == [1], "повторять разбор того же ответа незачем"
    assert failures == ["пакет 1: не разобрать ответ"]


def test_persistent_failure_is_reported(monkeypatch):
    """Отказ, переживший все заходы, попадает в отчёт, не теряется молча."""
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    attempts: list[int] = []

    def ask(batch):
        attempts.append(batch)
        raise llm.LlmError("провайдер лежит")

    answers, _, failures = llm.process_batches([7], ask, LOG, retry_rounds=2)

    assert answers == {}
    assert len(attempts) == 3, "основной проход плюс два повтора"
    # Номер в отчёте порядковый — по нему видно, какой пакет не дошёл.
    assert failures == ["пакет 1: провайдер лежит"]
