"""Учётные записи и сессии — основание этапа 9.

До этого модуля программа была инструментом на одного: кто дотянулся до
порта, тот и хозяин. Для сервиса этого мало, и добавлять вход задним числом
опаснее всего — забытая ручка означает не неудобство, а чужие записи
на экране.

**Три решения, которые стоит понимать.**

1. **Вход выключен по умолчанию.** Местная работа на своей машине не должна
   требовать пароля: это тот же инструмент, что был. Включается настройкой
   `auth.enabled`, и только тогда появляется разделение по владельцам.
   Один переключатель вместо двух веток поведения.

2. **Пароли — `scrypt` из стандартной библиотеки.** Не потому что он лучше
   argon2, а потому что не требует зависимости: у проекта нет сети на машине
   разработки, а лишний пакет в цепочке поставки — это ещё и риск (§66).
   Параметры взяты с запасом, соль своя у каждого.

3. **Сессия — строка в базе, а не подписанный кук.** Подписанный не отозвать:
   украденный работает до истечения срока. Строку можно удалить, и вход
   прекратится немедленно — для сервиса с чужими записями это важнее
   экономии на одном запросе к базе.

**Пространство хранения.** У каждого владельца своё: `projects/<workspace>/`.
Имя пространства выдаётся при создании и не меняется — по нему разложены
файлы, и переименование означало бы переезд каталога. У местной работы без
входа пространство остаётся прежним, `default`, поэтому уже заведённые записи
никуда не переезжают.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

#: Пространство местной работы без входа. То же имя, что было у проекта
#: с самого начала, поэтому включение учёток не трогает уже сделанное.
LOCAL_WORKSPACE = "default"

#: Параметры scrypt. n=2^15 — примерно 100 мс на подбор одного пароля на
#: нынешнем железе: достаточно медленно для перебора и незаметно при входе.
SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1

#: Сколько живёт сессия без обращений. Месяц: сервис, в который заходят раз
#: в неделю после стрима, не должен спрашивать пароль каждый раз.
SESSION_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    login      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    -- Пространство хранения. Неизменяемо: по нему разложены файлы.
    workspace  TEXT NOT NULL UNIQUE,
    password   TEXT NOT NULL,
    salt       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    -- Заложено сразу, чтобы не мигрировать схему ради первой же надобности.
    is_admin   INTEGER NOT NULL DEFAULT 0,
    disabled   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


class AccountError(ValueError):
    """Учётку завести или открыть нельзя, и причина понятна человеку."""


@dataclass(frozen=True)
class User:
    user_id: int
    login: str
    workspace: str
    is_admin: bool = False

    @property
    def is_local(self) -> bool:
        """Хозяин местной установки — когда вход выключен вовсе."""
        return self.user_id == 0


#: Кто работает, когда вход выключен. Не запись в базе: заводить учётку ради
#: одного человека на своей машине незачем, а код при этом везде одинаков —
#: владелец есть всегда, просто иногда он один.
LOCAL_USER = User(user_id=0, login="local", workspace=LOCAL_WORKSPACE, is_admin=True)


def _now() -> datetime:
    return datetime.now(UTC)


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


#: Предел памяти для scrypt. По умолчанию OpenSSL разрешает 32 МБ и падает
#: с «memory limit exceeded» ровно на выбранных параметрах: им нужно
#: 128 × n × r = 32 МБ ровно, впритык. Предел поднят с запасом.
SCRYPT_MAXMEM = 96 * 1024 * 1024


def hash_password(password: str, salt: str) -> str:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt),
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
        dklen=32,
    ).hex()


def create(
    connection: sqlite3.Connection,
    *,
    login: str,
    password: str,
    is_admin: bool = False,
) -> User:
    """Заводит учётку. Пространство выдаётся по логину и больше не меняется."""
    ensure_schema(connection)
    login = login.strip()
    if len(login) < 3:
        raise AccountError("логин короче трёх знаков")
    if len(password) < 8:
        raise AccountError("пароль короче восьми знаков")

    workspace = _workspace_for(connection, login)
    salt = secrets.token_hex(16)
    try:
        cursor = connection.execute(
            "INSERT INTO users (login, workspace, password, salt, created_at, is_admin)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (login, workspace, hash_password(password, salt), salt,
             _now().isoformat(timespec="seconds"), int(is_admin)),
        )
    except sqlite3.IntegrityError as exc:
        raise AccountError(f"учётка «{login}» уже есть") from exc
    connection.commit()
    return User(user_id=int(cursor.lastrowid), login=login, workspace=workspace, is_admin=is_admin)


def _workspace_for(connection: sqlite3.Connection, login: str) -> str:
    """Имя каталога по логину: только латиница, цифры и дефис.

    Логин может быть каким угодно, а имя каталога — нет: кириллица, пробелы
    и точки в путях однажды обязательно во что-нибудь упрутся. Совпадения
    разводятся числом, а не отказом в регистрации.
    """
    base = "".join(ch if ch.isascii() and (ch.isalnum() or ch == "-") else "-" for ch in login.lower())
    base = base.strip("-")[:24] or "user"
    if base == LOCAL_WORKSPACE:
        base = f"{base}-1"
    candidate, suffix = base, 1
    while connection.execute(
        "SELECT 1 FROM users WHERE workspace = ?", (candidate,)
    ).fetchone():
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def verify(connection: sqlite3.Connection, *, login: str, password: str) -> User | None:
    """Проверяет пароль. None — не сошлось; причина наружу не выдаётся.

    «Нет такого логина» и «неверный пароль» отвечают одинаково: разница между
    ними — готовый список тех, кто здесь зарегистрирован.
    """
    ensure_schema(connection)
    row = connection.execute(
        "SELECT * FROM users WHERE login = ? COLLATE NOCASE", (login.strip(),)
    ).fetchone()
    if row is None or row["disabled"]:
        # Считаем хэш и на пустом месте: без этого время ответа выдаёт,
        # существует логин или нет.
        hash_password(password, secrets.token_hex(16))
        return None
    expected = hash_password(password, row["salt"])
    if not hmac.compare_digest(expected, row["password"]):
        return None
    return _user(row)


def open_session(connection: sqlite3.Connection, user: User) -> str:
    ensure_schema(connection)
    token = secrets.token_urlsafe(32)
    now = _now()
    connection.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user.user_id, now.isoformat(timespec="seconds"),
         (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")),
    )
    connection.commit()
    return token


def close_session(connection: sqlite3.Connection, token: str) -> None:
    ensure_schema(connection)
    connection.execute("DELETE FROM sessions WHERE token = ?", (token,))
    connection.commit()


def user_for_token(connection: sqlite3.Connection, token: str | None) -> User | None:
    """Кто пришёл с этим ключом. Просроченная сессия удаляется на месте."""
    if not token:
        return None
    ensure_schema(connection)
    row = connection.execute(
        "SELECT s.expires_at, u.* FROM sessions s JOIN users u ON u.user_id = s.user_id"
        " WHERE s.token = ?",
        (token,),
    ).fetchone()
    if row is None or row["disabled"]:
        return None
    if datetime.fromisoformat(row["expires_at"]) < _now():
        close_session(connection, token)
        return None
    return _user(row)


def _user(row: Any) -> User:
    return User(
        user_id=int(row["user_id"]),
        login=row["login"],
        workspace=row["workspace"],
        is_admin=bool(row["is_admin"]),
    )


def listing(connection: sqlite3.Connection) -> list[User]:
    ensure_schema(connection)
    rows = connection.execute("SELECT * FROM users ORDER BY user_id").fetchall()
    return [_user(row) for row in rows]


def count(connection: sqlite3.Connection) -> int:
    ensure_schema(connection)
    return int(connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])
