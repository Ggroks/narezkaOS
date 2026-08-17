"""Кредиты: счёт, цены и списания.

Решение пользователя: оплата счётом, как у платного доступа к моделям,
но с условием — экономику нужно уметь поменять целиком, не потеряв
историю. Отсюда главное свойство, которое здесь проверяется: книга хранит
факты вместе с версией цен, по которой они посчитаны.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from narezka.core import credits, db
from narezka.core.config import BillingConfig


def rates(**kwargs) -> BillingConfig:
    base = {
        "enabled": True,
        "version": 1,
        "per_video_hour": {"analysis": 20.0, "shorts": 8.0, "long": 12.0},
        "minimum": 2.0,
    }
    return BillingConfig(**{**base, **kwargs})


def test_balance_is_the_sum_of_the_ledger(tmp_path: Path) -> None:
    """Баланс — сумма записей, а не отдельное поле: поле можно
    рассинхронизировать с историей и потом не узнать, где правда."""
    with db.connect(tmp_path) as connection:
        assert credits.balance(connection, "ivan") == 0
        credits.add(connection, workspace="ivan", amount=100)
        credits.add(connection, workspace="ivan", amount=50, kind="grant")
        assert credits.balance(connection, "ivan") == 150


def test_charge_is_proportional_to_hours(tmp_path: Path) -> None:
    quote = credits.quote_for(rates(), ["analysis"], 2 * 3600)
    assert quote.credits == 40.0
    half = credits.quote_for(rates(), ["analysis"], 3600)
    assert half.credits == 20.0


def test_assembly_costs_less_than_the_analysis(tmp_path: Path) -> None:
    """Расшифровка — три четверти всей работы, и она входит в разбор."""
    hour = 3600
    assert (
        credits.quote_for(rates(), ["shorts"], hour).credits
        < credits.quote_for(rates(), ["analysis"], hour).credits
    )


def test_short_record_pays_the_minimum(tmp_path: Path) -> None:
    """Трёхминутная запись всё равно требует запуска модели, скачивания
    и кодирования."""
    assert credits.quote_for(rates(), ["analysis"], 180).credits == 2.0


def test_unknown_duration_gives_a_quote_without_a_price(tmp_path: Path) -> None:
    """У только что добавленной ссылки длины ещё нет: точную цену назвать
    нельзя, и притворяться, что можно, — обман."""
    quote = credits.quote_for(rates(), ["analysis"], None)
    assert quote.known is False and quote.credits == 0


def test_charge_remembers_the_price_version(tmp_path: Path) -> None:
    """Без версии старые списания перестают быть объяснимыми при первом же
    изменении тарифа — а менять его пользователь хочет свободно."""
    with db.connect(tmp_path) as connection:
        credits.add(connection, workspace="ivan", amount=100)
        quote = credits.quote_for(rates(version=3), ["analysis"], 3600)
        credits.charge(connection, workspace="ivan", quote=quote, video_id="v1")

        entry = credits.history(connection, "ivan")[0]
        assert entry["kind"] == "charge" and entry["amount"] == -20.0
        assert entry["rate_version"] == 3 and entry["video_hours"] == 1.0


def test_price_change_does_not_touch_the_past(tmp_path: Path) -> None:
    with db.connect(tmp_path) as connection:
        credits.add(connection, workspace="ivan", amount=1000)
        credits.charge(
            connection, workspace="ivan",
            quote=credits.quote_for(rates(version=1), ["analysis"], 3600), video_id="v1",
        )
        credits.charge(
            connection, workspace="ivan",
            quote=credits.quote_for(
                rates(version=2, per_video_hour={"analysis": 50.0}), ["analysis"], 3600
            ),
            video_id="v2",
        )
        versions = {entry["rate_version"]: entry["amount"] for entry in credits.history(connection, "ivan")[:2]}
        assert versions == {2: -50.0, 1: -20.0}


def test_not_enough_credits_is_refused_before_the_work(tmp_path: Path) -> None:
    """Отказ на входе в очередь — там ещё ничего не потрачено."""
    with db.connect(tmp_path) as connection:
        credits.add(connection, workspace="ivan", amount=10)
        quote = credits.quote_for(rates(), ["analysis"], 3600)  # 20 кредитов
        with pytest.raises(credits.NotEnoughCredits):
            credits.ensure_enough(connection, workspace="ivan", quote=quote)


def test_empty_account_cannot_start_a_record_of_unknown_length(tmp_path: Path) -> None:
    with db.connect(tmp_path) as connection:
        quote = credits.quote_for(rates(), ["analysis"], None)
        with pytest.raises(credits.NotEnoughCredits):
            credits.ensure_enough(connection, workspace="ivan", quote=quote)

        credits.add(connection, workspace="ivan", amount=5)
        credits.ensure_enough(connection, workspace="ivan", quote=quote)  # не бросает


def test_charge_may_go_negative(tmp_path: Path) -> None:
    """Работа уже сделана: отказ записать списание сделал бы её бесплатной.
    Следующая задача упрётся в проверку — там ещё ничего не потрачено."""
    with db.connect(tmp_path) as connection:
        credits.add(connection, workspace="ivan", amount=5)
        credits.charge(
            connection, workspace="ivan",
            quote=credits.quote_for(rates(), ["analysis"], 3600), video_id="v1",
        )
        assert credits.balance(connection, "ivan") == -15.0
