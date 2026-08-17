"""Проверка ссылок перед скачиванием.

На своей машине ссылка — это то, что человек сам вставил. На сервере это
ввод постороннего, и по нему сервер сходит куда скажут: на `127.0.0.1`,
в служебную сеть, на адрес облачных метаданных `169.254.169.254`, откуда
у многих провайдеров выдаются ключи доступа. Класс называется SSRF,
в §68 он был отложен «до публичного запуска» — запуск наступил.

**Что проверяется всегда**, независимо от настроек:

- схема только `http` или `https`: `file://` читает диск сервера;
- в ссылке нет логина с паролем — их пишут, чтобы обмануть разбор адреса;
- имя разрешается в адрес, и ни один из полученных адресов не смотрит
  внутрь: петля, частные сети, локальная связь, служебные диапазоны.

**Чего эта проверка не делает.** Между проверкой и скачиванием имя может
разрешиться заново и уже в другой адрес — это называется перепривязкой DNS.
Закрыть её можно, только заставив загрузчик ходить по проверенному адресу,
а `yt-dlp` такого не умеет. Здесь это сказано прямо, а не умолчано: защита
закрывает обычную попытку, а не изощрённую.

**Список разрешённых площадок** — отдельная настройка поверх этого. По
умолчанию пуст: на своей машине ограничивать человека в источниках незачем.
Для сервиса он включается и оставляет ровно то, ради чего сервис сделан.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

#: Площадки, ради которых всё делалось. Список для настройки сервиса —
#: в коде он только предлагается, значение живёт в конфиге.
KNOWN_HOSTS = (
    "youtube.com",
    "youtu.be",
    "twitch.tv",
)


class SourceError(ValueError):
    """Ссылку принять нельзя, и причина понятна человеку."""


def _addresses(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SourceError(f"имя {host} не разрешается") from exc
    return sorted({info[4][0] for info in infos})


def _is_internal(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return True
    return (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_multicast
        or parsed.is_unspecified
    )


def check(url: str, *, allowed_hosts: tuple[str, ...] | list[str] = ()) -> str:
    """Возвращает ссылку, если по ней можно идти. Иначе — `SourceError`."""
    address = url.strip()
    parsed = urlparse(address)

    if parsed.scheme not in ("http", "https"):
        raise SourceError("ссылка должна начинаться с http:// или https://")
    if parsed.username or parsed.password:
        raise SourceError("ссылка с логином и паролем не принимается")

    host = parsed.hostname
    if not host:
        raise SourceError("в ссылке нет адреса площадки")

    if allowed_hosts and not _matches(host, allowed_hosts):
        listed = ", ".join(allowed_hosts)
        raise SourceError(f"скачиваем только с этих площадок: {listed}")

    for resolved in _addresses(host):
        if _is_internal(resolved):
            # Не называем адрес: это подсказка тому, кто прощупывает сеть.
            raise SourceError("эта ссылка ведёт внутрь сети сервера")

    return address


def _matches(host: str, allowed: tuple[str, ...] | list[str]) -> bool:
    """Совпадение по домену вместе с поддоменами.

    Сравнение по суффиксу с точкой, а не `endswith` по строке: иначе
    `notyoutube.com` прошёл бы как `youtube.com`.
    """
    host = host.lower().rstrip(".")
    for item in allowed:
        item = item.lower().strip().rstrip(".")
        if host == item or host.endswith(f".{item}"):
            return True
    return False
