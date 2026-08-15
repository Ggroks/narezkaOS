"""Тесты чтения чата Twitch (BAZA.md §41).

Сеть не трогается. Проверяется то, что ломается при смене чужого API:
разбор ссылки, разбор ответа и устойчивость к отсутствующим полям.
"""

from __future__ import annotations

import pytest

from narezka.core import twitch


def node(offset: float = 10.0, author: str = "viewer", text: str = "ахаха", **extra) -> dict:
    base = {
        "id": f"m{offset}",
        "contentOffsetSeconds": offset,
        "commenter": {"displayName": author},
        "message": {"fragments": [{"text": text}]},
    }
    base.update(extra)
    return base


# --- разбор ссылки ---------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.twitch.tv/videos/2846789827", "2846789827"),
        ("https://twitch.tv/videos/123/", "123"),
        ("https://www.twitch.tv/videos/123?t=1h2m", "123"),
        ("https://www.youtube.com/watch?v=abc", None),
        ("https://www.twitch.tv/somestreamer", None),
        ("", None),
    ],
)
def test_video_id_from_url(url: str, expected: str | None) -> None:
    assert twitch.video_id_from_url(url) == expected


# --- разбор сообщения ------------------------------------------------------


def test_message_is_parsed() -> None:
    message = twitch._parse(node(42.0, "Вася", "это было мощно"))
    assert message == {"at": 42.0, "author": "Вася", "text": "это было мощно", "is_bot": False}


def test_fragments_are_joined() -> None:
    """Сообщение со смайлами приходит разбитым на куски."""
    raw = node()
    raw["message"]["fragments"] = [{"text": "ну "}, {"text": "ты "}, {"text": "даёшь"}]
    assert twitch._parse(raw)["text"] == "ну ты даёшь"


def test_bots_are_marked() -> None:
    """Боты пишут по расписанию: их всплеск не связан с происходящим."""
    assert twitch._parse(node(author="WizeBot"))["is_bot"] is True
    assert twitch._parse(node(author="nightbot"))["is_bot"] is True
    assert twitch._parse(node(author="обычныйзритель"))["is_bot"] is False


def test_empty_message_is_dropped() -> None:
    raw = node()
    raw["message"]["fragments"] = [{"text": "   "}]
    assert twitch._parse(raw) is None


def test_message_without_offset_is_dropped() -> None:
    """Без метки времени сообщение бесполезно как сигнал."""
    raw = node()
    del raw["contentOffsetSeconds"]
    assert twitch._parse(raw) is None


def test_missing_commenter_does_not_crash() -> None:
    """Удалённый аккаунт приходит без commenter — это не повод падать."""
    raw = node()
    raw["commenter"] = None
    assert twitch._parse(raw)["author"] == ""


def test_login_is_used_when_display_name_is_absent() -> None:
    raw = node()
    raw["commenter"] = {"login": "someone"}
    assert twitch._parse(raw)["author"] == "someone"
