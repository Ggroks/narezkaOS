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


def workspace_for(request: Request, config, asked: str | None = None) -> str:
    """Пространство хранения для этого запроса.

    При включённом входе — только своё, что бы ни просил клиент. При
    выключенном — то, что попросили: у консоли есть `--project`, и это
    полезное разделение на своей машине.
    """
    user = getattr(request.state, "user", None)
    if config.auth.enabled:
        return (user or LOCAL_USER).workspace
    return asked or config.default_project


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
