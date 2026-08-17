"""Кредиты: счёт, списания и цена работы.

Решение пользователя (17 августа 2026): оплата не подпиской, а счётом
в кредитах — пополнил и тратишь, как у платного доступа к моделям.
С оговоркой, которая определила устройство модуля: **экономику нужно уметь
поменять целиком**, не потеряв историю.

Отсюда разделение на две вещи, которые обычно путают:

1. **Книга** — что произошло: пополнено столько-то, списано столько-то,
   за такую-то работу, по такой-то версии цен. Это факты, они не меняются
   никогда. Баланс — не поле, которое правится, а сумма записей: поле можно
   рассинхронизировать с историей, сумму — нет.
2. **Цены** — политика: сколько стоит час записи в каждом куске работы.
   Живут в конфиге и версионируются. Меняются свободно, потому что каждая
   запись в книге помнит версию, по которой посчитана, и старые списания
   остаются объяснимыми.

**За что берётся плата.** За работу, которая действительно выполнилась.
Стадии кэшируются, и повторное нажатие обычно не делает ничего — брать
за это деньги значило бы штрафовать за осторожность. Поэтому считаются
только те куски работы, где хоть одна стадия отработала заново.

**Неудача не оплачивается.** Упавший прогон съел машинное время, но человек
не получил ничего. Это осознанный выбор в его пользу: цена ошибки сервиса
не должна ложиться на того, кто её не совершал.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

#: Виды записей в книге. Пополнение и подарок разведены намеренно: подарок
#: при регистрации — это расход владельца сервиса, а не деньги человека,
#: и в отчётах их складывать нельзя.
KINDS = ("topup", "grant", "charge", "refund")

SCHEMA = """
CREATE TABLE IF NOT EXISTS credits_ledger (
    entry_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace     TEXT NOT NULL,
    at            TEXT NOT NULL,
    kind          TEXT NOT NULL,
    -- Положительное пополняет, отрицательное списывает. Баланс — это сумма.
    amount        REAL NOT NULL,
    -- За что списано. Пусто у пополнений.
    task_id       TEXT,
    video_id      TEXT,
    work_groups   TEXT,
    video_seconds REAL,
    -- Версия цен, по которой посчитано. Без неё старые списания
    -- перестают быть объяснимыми при первом же изменении тарифа.
    rate_version  INTEGER,
    note          TEXT
);

CREATE INDEX IF NOT EXISTS idx_ledger_space ON credits_ledger(workspace, at);
"""


class NotEnoughCredits(Exception):
    """Не хватает кредитов на работу."""

    def __init__(self, need: float, have: float) -> None:
        super().__init__(f"нужно {need:.0f} кредитов, на счету {have:.0f}")
        self.need = need
        self.have = have


@dataclass(frozen=True)
class Quote:
    """Оценка стоимости работы — верхняя граница, а не точная цена.

    Точную назвать нельзя: часть стадий возьмётся из кэша и не будет стоить
    ничего, а у только что добавленной ссылки неизвестна даже длина записи.
    Поэтому наружу идёт «не больше», и списание никогда его не превышает —
    обратный порядок был бы обманом.
    """

    credits: float
    #: Длительность записи, по которой считали. None — ещё неизвестна.
    video_seconds: float | None
    breakdown: dict[str, float]
    rate_version: int

    @property
    def known(self) -> bool:
        return self.video_seconds is not None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def balance(connection: sqlite3.Connection, workspace: str) -> float:
    """Счёт — сумма записей книги, а не отдельное поле.

    Поле можно рассинхронизировать с историей одной незакрытой транзакцией
    и потом никогда не узнать, где правда. Сумму — нельзя.
    """
    ensure_schema(connection)
    row = connection.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM credits_ledger WHERE workspace = ?",
        (workspace,),
    ).fetchone()
    return round(float(row["total"]), 3)


def add(
    connection: sqlite3.Connection,
    *,
    workspace: str,
    amount: float,
    kind: str = "topup",
    note: str | None = None,
) -> float:
    """Пополнение или подарок. Возвращает новый баланс."""
    if kind not in KINDS:
        raise ValueError(f"неизвестный вид записи: {kind}")
    if amount <= 0:
        raise ValueError("пополнение должно быть положительным")
    ensure_schema(connection)
    connection.execute(
        "INSERT INTO credits_ledger (workspace, at, kind, amount, note) VALUES (?, ?, ?, ?, ?)",
        (workspace, _now(), kind, float(amount), note),
    )
    connection.commit()
    return balance(connection, workspace)


def charge(
    connection: sqlite3.Connection,
    *,
    workspace: str,
    quote: Quote,
    task_id: str | None = None,
    video_id: str | None = None,
    note: str | None = None,
) -> float:
    """Списание за выполненную работу. Возвращает новый баланс.

    Уходить в минус разрешено: работа уже сделана, и отказ записать
    списание означал бы просто её бесплатность. Следующая задача не
    начнётся, пока счёт не пополнят, — и это правильное место для отказа,
    потому что там ещё ничего не потрачено.
    """
    if quote.credits <= 0:
        return balance(connection, workspace)
    ensure_schema(connection)
    connection.execute(
        "INSERT INTO credits_ledger (workspace, at, kind, amount, task_id, video_id,"
        " work_groups, video_seconds, rate_version, note)"
        " VALUES (?, ?, 'charge', ?, ?, ?, ?, ?, ?, ?)",
        (
            workspace, _now(), -abs(float(quote.credits)), task_id, video_id,
            ",".join(sorted(quote.breakdown)), quote.video_seconds,
            quote.rate_version, note,
        ),
    )
    connection.commit()
    return balance(connection, workspace)


def history(connection: sqlite3.Connection, workspace: str, limit: int = 50) -> list[dict[str, Any]]:
    ensure_schema(connection)
    rows = connection.execute(
        "SELECT * FROM credits_ledger WHERE workspace = ? ORDER BY entry_id DESC LIMIT ?",
        (workspace, limit),
    ).fetchall()
    return [
        {
            "at": row["at"],
            "kind": row["kind"],
            "amount": round(row["amount"], 2),
            "video_id": row["video_id"],
            "work_groups": row["work_groups"],
            "video_hours": round((row["video_seconds"] or 0) / 3600, 2) or None,
            "rate_version": row["rate_version"],
            "note": row["note"],
        }
        for row in rows
    ]


# --- цены -------------------------------------------------------------------


def quote_for(rates, groups, video_seconds: float | None) -> Quote:
    """Считает стоимость работы по действующим ценам.

    `groups` — куски работы, за которые берётся плата. Для оценки перед
    запуском это всё, что может выполниться; для списания — только то,
    что выполнилось на самом деле.
    """
    hours = (video_seconds or 0) / 3600
    breakdown = {
        group: round(rates.per_video_hour.get(group, 0.0) * hours, 3)
        for group in sorted(set(groups))
    }
    total = sum(breakdown.values())
    if total > 0:
        # Минимум за задачу: короткая запись всё равно занимает машину
        # запуском модели, скачиванием и кодированием.
        total = max(total, rates.minimum)
    return Quote(
        credits=round(total, 2),
        video_seconds=video_seconds,
        breakdown=breakdown,
        rate_version=rates.version,
    )


def ensure_enough(connection: sqlite3.Connection, *, workspace: str, quote: Quote) -> None:
    """Проверка перед постановкой в очередь — там ещё ничего не потрачено.

    У только что добавленной ссылки длина записи неизвестна, и точную цену
    назвать нельзя. Тогда достаточно, чтобы счёт был не в минусе: работа
    спишется по факту, а следующая уже упрётся в проверку.
    """
    have = balance(connection, workspace)
    need = quote.credits if quote.known else 0.0
    if have < need or (not quote.known and have <= 0):
        raise NotEnoughCredits(max(need, 1.0), have)
