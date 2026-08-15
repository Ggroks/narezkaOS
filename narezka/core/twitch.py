"""Повтор чата Twitch VOD.

BAZA.md §41. Чат — самый дешёвый и один из самых сильных сигналов интереса:
всплеск сообщений почти всегда совпадает с тем, что зрители сочли важным.
Считается на CPU, ничего не стоит и не требует модели.

**Эндпоинт неофициальный.** Публичного API повтора чата у Twitch нет; здесь
используется тот же GraphQL, что и веб-плеер. Отсюда два следствия, заложенных
в код: стадия опциональна и падение переживается, а разбор ответа не полагается
на присутствие полей.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import httpx

GQL_URL = "https://gql.twitch.tv/gql"

#: Публичный идентификатор веб-плеера. Не секрет и не выданный нам ключ —
#: это то же значение, что уходит из браузера при открытии записи.
WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

#: Хэш сохранённого запроса. Задаётся Twitch и может смениться — тогда
#: эндпоинт ответит ошибкой, и стадия честно пропустится.
PERSISTED_QUERY_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"

#: Пауза между запросами. Мы читаем чужой сервис без договорённости
#: об объёме — не наваливаться на него хотя бы вежливо.
PAGE_DELAY = 0.15

#: Сколько раз повторить запрос при временном отказе сервиса. Чужой сервис
#: изредка отвечает «service error» без причины — терять из-за этого хвост
#: записи незачем.
MAX_RETRIES = 3
RETRY_DELAY = 1.5

#: На сколько сдвигаться, если окно не продвинулось само. Бывает при очень
#: плотном чате: в ответ помещается меньше сообщений, чем пришло за секунду.
#: Без принудительного шага запрос повторялся бы бесконечно.
FORCED_STEP = 1.0

#: Боты пишут по расписанию, а не в ответ на происходящее. Их сообщения
#: размывают сигнал: реклама раз в десять минут выглядит как ровный фон,
#: а приветствие новому зрителю — как всплеск на пустом месте.
KNOWN_BOTS = frozenset(
    {
        "wizebot", "nightbot", "streamelements", "streamlabs", "moobot",
        "fossabot", "sery_bot", "botrixoficial", "own3d", "kofistreambot",
    }
)


class TwitchError(RuntimeError):
    """Не удалось получить чат."""


class TwitchTemporaryError(TwitchError):
    """Временный отказ сервиса: лечится повтором, а не правкой запроса."""


def video_id_from_url(url: str) -> str | None:
    """Числовой идентификатор записи из ссылки вида twitch.tv/videos/123."""
    text = (url or "").strip().rstrip("/")
    if "twitch.tv" not in text or "/videos/" not in text:
        return None
    tail = text.split("/videos/", 1)[1].split("?", 1)[0].split("/", 1)[0]
    return tail if tail.isdigit() else None


def _page(client: httpx.Client, video_id: str, cursor: str | None, offset: float) -> dict[str, Any]:
    variables: dict[str, Any] = {"videoID": video_id}
    if cursor:
        variables["cursor"] = cursor
    else:
        variables["contentOffsetSeconds"] = int(offset)

    response = client.post(
        GQL_URL,
        json={
            "operationName": "VideoCommentsByOffsetOrCursor",
            "variables": variables,
            "extensions": {
                "persistedQuery": {"version": 1, "sha256Hash": PERSISTED_QUERY_HASH}
            },
        },
    )
    if response.status_code != 200:
        raise TwitchError(f"чат вернул {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise TwitchError(f"чат вернул не JSON: {exc}") from exc

    if payload.get("errors"):
        text = str(payload["errors"])
        if "service error" in text.lower():
            raise TwitchTemporaryError(text)
        raise TwitchError(f"чат вернул ошибку: {text}")

    video = (payload.get("data") or {}).get("video")
    if not video:
        raise TwitchError("записи нет или чат недоступен")
    return video.get("comments") or {}


def fetch_messages(
    video_id: str,
    *,
    duration_seconds: float | None = None,
    timeout: float = 30.0,
    max_requests: int = 6000,
    on_progress: Any = None,
) -> Iterator[dict[str, Any]]:
    """Сообщения чата по порядку.

    Идёт **по смещению внутри записи**, а не по курсору постраничности.
    Курсор упирается в проверку целостности Twitch — защиту от автоматизации,
    которую мы не обходим. Запрос по смещению делает и обычный плеер, когда
    зритель перематывает запись, поэтому этот путь остаётся открытым.

    Генератор, а не список: у восьмичасовой записи сообщений сотни тысяч,
    и держать их все в памяти до записи на диск незачем.
    """
    headers = {"Client-ID": WEB_CLIENT_ID, "Content-Type": "application/json"}
    offset = 0.0
    seen_ids: set[str] = set()
    seen = 0

    with httpx.Client(timeout=timeout, headers=headers) as client:
        for request in range(max_requests):
            comments = _fetch_with_retry(client, video_id, offset)
            edges = comments.get("edges") or []
            if not edges:
                return

            last_offset = offset
            for edge in edges:
                node = edge.get("node") or {}
                message = _parse(node)
                if not message:
                    continue
                # Окна соседних запросов перекрываются — без отсева
                # пограничные сообщения посчитались бы дважды и раздули
                # всплеск там, где его нет.
                identifier = node.get("id") or f"{message['at']}:{message['author']}:{message['text']}"
                if identifier in seen_ids:
                    continue
                seen_ids.add(identifier)
                seen += 1
                last_offset = max(last_offset, message["at"])
                yield message

            if on_progress and request % 20 == 0:
                on_progress(seen, offset)

            if duration_seconds and last_offset >= duration_seconds:
                return
            # Окно не продвинулось — двигаем сами, иначе запрос повторялся бы
            # бесконечно на очень плотном участке чата.
            offset = last_offset if last_offset > offset else offset + FORCED_STEP
            time.sleep(PAGE_DELAY)


def _fetch_with_retry(client: httpx.Client, video_id: str, offset: float) -> dict[str, Any]:
    """Запрос с повторами при временном отказе."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _page(client, video_id, None, offset)
        except TwitchTemporaryError:
            if attempt >= MAX_RETRIES:
                raise
            time.sleep(RETRY_DELAY * (attempt + 1))
    raise TwitchTemporaryError("повторы исчерпаны")


def _parse(node: dict[str, Any]) -> dict[str, Any] | None:
    offset = node.get("contentOffsetSeconds")
    if offset is None:
        return None

    commenter = node.get("commenter") or {}
    author = commenter.get("displayName") or commenter.get("login") or ""
    fragments = (node.get("message") or {}).get("fragments") or []
    text = "".join(fragment.get("text") or "" for fragment in fragments).strip()

    if not text:
        return None
    return {
        "at": round(float(offset), 2),
        "author": author,
        "text": text,
        "is_bot": author.lower() in KNOWN_BOTS,
    }
