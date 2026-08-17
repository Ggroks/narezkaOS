"""Вход и разделение по владельцам (этап 9A).

Главное здесь — не «работает ли форма входа», а два свойства, ошибка
в которых означает чужие записи на экране:

1. **Запрет по умолчанию.** Ручка, о которой забыли, обязана отвечать 401,
   а не отдавать данные.
2. **Пространство берётся из сессии.** Клиентский `project` при включённом
   входе игнорируется, а не проверяется: проверку можно забыть в одной ручке
   из тридцати, подстановку в одном месте — нет.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from narezka.api import app as api_app
from narezka.core import accounts, db
from narezka.core.config import load_config
from narezka.core.device import DeviceInfo


def make_client(tmp_path: Path, monkeypatch, *, auth_enabled: bool):
    config = load_config().model_copy(update={"storage_root": tmp_path})
    config.auth.enabled = auth_enabled
    config.auth.secure_cookie = False
    device = DeviceInfo(kind="cpu", name="test", detail="тест")
    monkeypatch.setattr(api_app, "_config", lambda profile=None: (config, device))
    return TestClient(api_app.app), config


@pytest.fixture
def guarded(tmp_path: Path, monkeypatch):
    """Сервер с включённым входом и двумя заведёнными людьми."""
    client, config = make_client(tmp_path, monkeypatch, auth_enabled=True)
    with db.connect(config.storage_root) as connection:
        accounts.create(connection, login="ivan", password="parol-ivana")
        accounts.create(connection, login="petr", password="parol-petra")
    return client


def enter(client: TestClient, login: str, password: str) -> TestClient:
    response = client.post("/api/auth/login", json={"login": login, "password": password})
    assert response.status_code == 200, response.text
    return client


# --- запрет по умолчанию ---------------------------------------------------


def test_everything_is_closed_without_login(guarded) -> None:
    for path in ("/api/videos", "/api/stages", "/api/groups", "/api/settings/models"):
        assert guarded.get(path).status_code == 401, path
    assert guarded.post("/api/videos", json={"url": "https://twitch.tv/videos/1"}).status_code == 401


def test_only_four_things_are_open(guarded) -> None:
    """Проверка живости и вход — иначе на страницу входа не попасть."""
    assert guarded.get("/api/health").status_code == 200
    assert guarded.get("/api/auth/me").json()["auth_required"] is True
    assert guarded.get("/api/auth/me").json()["user"] is None


def test_wrong_password_says_the_same_as_wrong_login(guarded) -> None:
    """Разные ответы выдали бы список зарегистрированных."""
    a = guarded.post("/api/auth/login", json={"login": "ivan", "password": "не тот"})
    b = guarded.post("/api/auth/login", json={"login": "нетакого", "password": "не тот"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_login_opens_the_rest(guarded) -> None:
    enter(guarded, "ivan", "parol-ivana")
    assert guarded.get("/api/videos").status_code == 200
    assert guarded.get("/api/auth/me").json()["user"]["login"] == "ivan"


def test_logout_closes_it_back(guarded) -> None:
    enter(guarded, "ivan", "parol-ivana")
    guarded.post("/api/auth/logout")
    assert guarded.get("/api/videos").status_code == 401


# --- изоляция --------------------------------------------------------------


def test_records_do_not_leak_between_people(guarded, tmp_path: Path) -> None:
    enter(guarded, "ivan", "parol-ivana")
    guarded.post("/api/videos", json={"url": "https://twitch.tv/videos/1", "title": "Иваново"})
    assert [v["title"] for v in guarded.get("/api/videos").json()] == ["Иваново"]

    guarded.post("/api/auth/logout")
    enter(guarded, "petr", "parol-petra")
    assert guarded.get("/api/videos").json() == [], "чужая запись видна в каталоге"


def test_same_url_gives_each_his_own_record(guarded, tmp_path: Path) -> None:
    """Идентификатор выводится из ссылки, поэтому у двоих он совпадёт.

    Разойтись они обязаны пространством хранения, иначе двое, разбирающие
    один и тот же стрим, работают над одной папкой.
    """
    enter(guarded, "ivan", "parol-ivana")
    first = guarded.post("/api/videos", json={"url": "https://twitch.tv/videos/7"}).json()
    guarded.post("/api/auth/logout")

    enter(guarded, "petr", "parol-petra")
    second = guarded.post("/api/videos", json={"url": "https://twitch.tv/videos/7"}).json()

    assert first["video_id"] == second["video_id"]
    spaces = sorted(p.name for p in (tmp_path / "projects").iterdir())
    assert spaces == ["ivan", "petr"]


def test_client_cannot_ask_for_a_foreign_workspace(guarded) -> None:
    """Главная проверка: `project` в запросе при включённом входе не значит
    ничего. Иначе достаточно подменить один параметр."""
    enter(guarded, "ivan", "parol-ivana")
    guarded.post("/api/videos", json={"url": "https://twitch.tv/videos/1", "title": "Иваново"})

    guarded.post("/api/auth/logout")
    enter(guarded, "petr", "parol-petra")
    assert guarded.get("/api/videos?project=ivan").json() == []


def test_foreign_record_is_not_reachable_by_id(guarded) -> None:
    """Знание идентификатора не должно давать доступ: он выводится из ссылки,
    то есть угадывается по одному только адресу стрима."""
    enter(guarded, "ivan", "parol-ivana")
    added = guarded.post("/api/videos", json={"url": "https://twitch.tv/videos/9"}).json()
    guarded.post("/api/auth/logout")

    enter(guarded, "petr", "parol-petra")
    video = added["video_id"]
    assert guarded.get(f"/api/videos/{video}").status_code == 404
    assert guarded.get(f"/api/videos/{video}/media").status_code == 404
    assert guarded.delete(f"/api/videos/{video}").status_code == 404


# --- местная работа не сломана ---------------------------------------------


def test_local_work_needs_no_login(tmp_path: Path, monkeypatch) -> None:
    """Выключенный вход оставляет прежний инструмент на одного: пароль
    на своей машине — это регресс, а не безопасность."""
    client, _ = make_client(tmp_path, monkeypatch, auth_enabled=False)
    assert client.get("/api/videos").status_code == 200
    body = client.get("/api/auth/me").json()
    assert body["auth_required"] is False and body["user"]["local"] is True


def test_local_work_keeps_the_old_storage_layout(tmp_path: Path, monkeypatch) -> None:
    """Уже заведённые записи лежат в projects/default и никуда не переезжают."""
    client, _ = make_client(tmp_path, monkeypatch, auth_enabled=False)
    client.post("/api/videos", json={"url": "https://twitch.tv/videos/1"})
    assert (tmp_path / "projects" / "default" / "videos").is_dir()


# --- сессии ----------------------------------------------------------------


def test_session_is_revocable(tmp_path: Path, monkeypatch) -> None:
    """Сессия — строка в базе, а не подписанный кук: украденный ключ должен
    отзываться, а не работать до истечения срока."""
    client, config = make_client(tmp_path, monkeypatch, auth_enabled=True)
    with db.connect(config.storage_root) as connection:
        user = accounts.create(connection, login="ivan", password="parol-ivana")
    enter(client, "ivan", "parol-ivana")
    assert client.get("/api/videos").status_code == 200

    with db.connect(config.storage_root) as connection:
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user.user_id,))
        connection.commit()
    assert client.get("/api/videos").status_code == 401


def test_password_is_not_stored_as_is(tmp_path: Path) -> None:
    with db.connect(tmp_path) as connection:
        accounts.create(connection, login="ivan", password="parol-ivana")
        row = connection.execute("SELECT password, salt FROM users").fetchone()
    assert "parol-ivana" not in row["password"]
    assert len(row["salt"]) == 32


def test_short_credentials_are_refused(tmp_path: Path) -> None:
    with db.connect(tmp_path) as connection:
        with pytest.raises(accounts.AccountError):
            accounts.create(connection, login="ab", password="достаточно длинный")
        with pytest.raises(accounts.AccountError):
            accounts.create(connection, login="normal", password="корот")


def test_workspace_is_safe_for_the_filesystem(tmp_path: Path) -> None:
    """Логин может быть каким угодно, имя каталога — нет: кириллица, точки
    и слэши в путях однажды обязательно во что-нибудь упрутся."""
    with db.connect(tmp_path) as connection:
        first = accounts.create(connection, login="Вася../etc", password="parol-vasi")
        second = accounts.create(connection, login="Петя..", password="parol-peti")
    for user in (first, second):
        assert user.workspace.replace("-", "").isalnum(), user.workspace
    assert first.workspace != second.workspace


# --- перебор паролей -------------------------------------------------------


def test_brute_force_gets_locked_out(guarded) -> None:
    """Пять попыток человек, забывший раскладку, укладывается; перебор
    словаря при паузе в четверть часа становится делом на годы."""
    from narezka.api import auth as api_auth

    api_auth._attempts.clear()
    for _ in range(api_auth.MAX_ATTEMPTS):
        assert guarded.post(
            "/api/auth/login", json={"login": "ivan", "password": "мимо"}
        ).status_code == 401

    blocked = guarded.post("/api/auth/login", json={"login": "ivan", "password": "мимо"})
    assert blocked.status_code == 429
    # Даже верный пароль теперь ждёт: иначе счётчик обходится одной удачей.
    assert guarded.post(
        "/api/auth/login", json={"login": "ivan", "password": "parol-ivana"}
    ).status_code == 429
    api_auth._attempts.clear()


def test_lockout_does_not_touch_the_neighbour(guarded) -> None:
    """Стуча в чужую учётку, нельзя запереть её хозяина: счёт ведётся
    по паре «откуда стучатся» и «в какую дверь»."""
    from narezka.api import auth as api_auth

    api_auth._attempts.clear()
    for _ in range(api_auth.MAX_ATTEMPTS):
        guarded.post("/api/auth/login", json={"login": "ivan", "password": "мимо"})

    assert guarded.post(
        "/api/auth/login", json={"login": "petr", "password": "parol-petra"}
    ).status_code == 200
    api_auth._attempts.clear()


def test_successful_login_clears_the_counter(guarded) -> None:
    from narezka.api import auth as api_auth

    api_auth._attempts.clear()
    for _ in range(api_auth.MAX_ATTEMPTS - 1):
        guarded.post("/api/auth/login", json={"login": "ivan", "password": "мимо"})
    assert guarded.post(
        "/api/auth/login", json={"login": "ivan", "password": "parol-ivana"}
    ).status_code == 200

    for _ in range(api_auth.MAX_ATTEMPTS - 1):
        assert guarded.post(
            "/api/auth/login", json={"login": "ivan", "password": "мимо"}
        ).status_code == 401
    api_auth._attempts.clear()


# --- загрузка файла --------------------------------------------------------


def test_upload_accepts_a_file_and_registers_it(guarded, tmp_path: Path) -> None:
    enter(guarded, "ivan", "parol-ivana")
    payload = b"\x00" * 4096
    body = guarded.post("/api/videos/upload?name=stream.mp4&title=Загруженный", content=payload)
    assert body.status_code == 200, body.text
    assert body.json()["size_bytes"] == len(payload)
    assert [v["title"] for v in guarded.get("/api/videos").json()] == ["Загруженный"]


def test_upload_keeps_only_the_extension_from_the_name(guarded, tmp_path: Path) -> None:
    """Имя приходит от постороннего и в путь на диске попадать не должно."""
    enter(guarded, "ivan", "parol-ivana")
    body = guarded.post("/api/videos/upload?name=../../побег.mp4", content=b"\x00" * 2048)
    assert body.status_code == 200
    stored = tmp_path / "projects" / "ivan" / "videos" / body.json()["video_id"] / "source"
    assert [p.suffix for p in stored.iterdir()] == [".mp4"]
    assert not any("побег" in p.name for p in stored.iterdir())


def test_upload_without_extension_is_refused(guarded) -> None:
    enter(guarded, "ivan", "parol-ivana")
    assert guarded.post("/api/videos/upload?name=stream", content=b"\x00" * 16).status_code == 400


def test_empty_upload_is_refused(guarded) -> None:
    enter(guarded, "ivan", "parol-ivana")
    assert guarded.post("/api/videos/upload?name=stream.mp4", content=b"").status_code == 400


def test_upload_needs_login(guarded) -> None:
    assert guarded.post("/api/videos/upload?name=x.mp4", content=b"\x00").status_code == 401


def test_server_refuses_a_path_when_login_is_on(guarded) -> None:
    """Главное отличие сервера от своей машины: путь читать нельзя."""
    enter(guarded, "ivan", "parol-ivana")
    response = guarded.post("/api/videos", json={"file": "/etc/passwd"})
    assert response.status_code == 400 and "загрузить" in response.json()["detail"]
