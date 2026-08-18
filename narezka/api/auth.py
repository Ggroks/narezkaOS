"""Кто пришёл и что ему можно.

**Запрет по умолчанию.** Доступ открыт явным списком, а не закрыт им.
Порядок именно такой, потому что ошибки у этих двух подходов разной цены:
забытая ручка при запрете по умолчанию перестаёт работать и об этом сразу
скажут, а при разрешении по умолчанию она молча отдаёт чужие записи. Первое
чинится за минуту, второе узнаётся от пользователя.

**Пространство берётся из сессии, а не из запроса.** Параметр `project`
ручки по-прежнему принимают — он был там с самого начала и им пользуется
консоль, — но при включённом входе он игнорируется. Проверять его было бы
ошибкой: проверку можно забыть в одной ручке из тридцати, а подстановку
поверх — нет, она в одном месте.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import Request

from narezka.core import accounts, db
from narezka.core.accounts import LOCAL_USER, User

#: Имя кука с ключом сессии.
COOKIE = "narezka_session"

#: Что доступно без входа. Ровно четыре вещи: проверка живости, сам вход,
#: вопрос «кто я» (интерфейс задаёт его, чтобы показать форму входа) и
#: регистрация, если она открыта.
PUBLIC_PATHS = frozenset({
    "/api/health",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/auth/signup",
})


def is_public(path: str) -> bool:
    """Открытый ли путь. Всё, что не начинается с /api, — это интерфейс.

    Статику закрывать смысла нет: в собранном фронтенде нет ничьих данных,
    а закрытая страница входа — это страница, на которую нельзя войти.
    """
    return path in PUBLIC_PATHS or not path.startswith("/api")


def current_user(request: Request, config) -> User | None:
    """Кто работает. None — вход требуется, но его нет.

    При выключенном входе всегда возвращается хозяин местной установки:
    остальной код не должен знать, включены учётки или нет.
    """
    if not config.auth.enabled:
        return LOCAL_USER
    token = request.cookies.get(COOKIE)
    with db.connect(config.storage_root) as connection:
        return accounts.user_for_token(connection, token)


def describe(user: User | None, config) -> dict[str, Any]:
    """Ответ на вопрос «кто я» — то, по чему интерфейс решает, показывать
    ли форму входа."""
    return {
        "auth_required": bool(config.auth.enabled),
        "allow_signup": bool(config.auth.enabled and config.auth.allow_signup),
        "user": None if user is None else {
            "login": user.login,
            "workspace": user.workspace,
            "is_admin": user.is_admin,
            "local": user.is_local,
        },
    }


# --- защита от перебора -----------------------------------------------------

#: Сколько неудачных попыток подряд терпим и как долго потом отдыхаем.
#: Пять попыток — человек, забывший раскладку, укладывается; перебор словаря
#: при паузе в четверть часа становится делом на годы.
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 900

_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()


def _key(request: Request, login: str) -> str:
    """Считаем по паре «кто стучится» и «в какую дверь».

    Только по логину — и чужую учётку можно запереть, стуча в неё наугад.
    Только по адресу — и перебор идёт из любой сети с одного адреса
    по всем логинам сразу.
    """
    client = request.client.host if request.client else "?"
    return f"{client}|{login.strip().lower()}"


def too_many(request: Request, login: str) -> int:
    """Сколько секунд ждать. Ноль — можно пробовать.

    Счётчик живёт в памяти процесса: перезапуск сервера его обнуляет, и это
    осознанное упрощение — от перебора защищает пауза, а не вечная память
    о попытках. Когда рабочих процессов станет несколько (9B), счётчик
    переедет в базу вместе с очередью.
    """
    now = time.monotonic()
    with _attempts_lock:
        recent = [at for at in _attempts.get(_key(request, login), []) if now - at < LOCKOUT_SECONDS]
        _attempts[_key(request, login)] = recent
        if len(recent) < MAX_ATTEMPTS:
            return 0
        return int(LOCKOUT_SECONDS - (now - recent[0])) + 1


def note_failure(request: Request, login: str) -> None:
    with _attempts_lock:
        _attempts.setdefault(_key(request, login), []).append(time.monotonic())


def note_success(request: Request, login: str) -> None:
    with _attempts_lock:
        _attempts.pop(_key(request, login), None)
