"""HTTP API.

BAZA.md §33: интерфейс работает поверх того же слоя, что и CLI — ни одной
операции только в одном месте. Каждый эндпоинт здесь вызывает те же функции
ядра, что и команда в консоли.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from narezka.api import auth, jobs, worker
from narezka.api.media import serve_file
from narezka.core import env
from narezka.core.clips import load_clips
from narezka.core.cuts import moment_in_clip
from narezka.core.edl import Edl
from narezka.core.artifacts import Artifact
from narezka.core.config import FramingConfig, load_config
from narezka.core.device import detect_device
from narezka.core.logging import get_logger
from narezka.core.framing import (
    Framing,
    build_layout_filter,
    plan_camera,
    plan_pip,
    plan_split,
    plan_track_still,
    describe,
    plan_frame,
    preview_presets,
)
from narezka.core import fonts
from narezka.core import framing as framing_core
from narezka.core import subtitles as subs
from narezka.core.media import MediaError, find_source, run_tool
from narezka.core import (
    accounts, credits, db, detectors, feedback, llm, publish, queue, registry, review,
    settings,
)
from narezka.core.paths import list_videos, video_paths
from narezka.core.stage import StageContext
from narezka.stages import GROUPS, PIPELINE, get_stage, stages_for
from narezka.stages.titles import STAGE_TITLES
from narezka.stages.render import load_framing, load_options, source_size

log = get_logger("api")

#: Рабочие потоки. Поднимаются вместе с сервером, а не при импорте: тесты
#: работают с очередью напрямую, и фоновая обработка им только мешала бы.
WORKERS: list[Any] = []


@asynccontextmanager
async def lifespan(_: FastAPI):
    config, _device = _config()
    WORKERS.extend(worker.start(_config, config.queue.parallel))
    log.info("рабочих потоков: %d", len(WORKERS))
    try:
        yield
    finally:
        for item in WORKERS:
            item.shutdown()
        WORKERS.clear()


app = FastAPI(title="Narezka OS", version="0.1.0", lifespan=lifespan)

# Дев-режим: фронтенд поднимается отдельным сервером Vite.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _config(profile: str | None = None):
    probe = load_config()
    device = detect_device(probe.device)
    return load_config(profile_override=profile, has_accelerator=device.is_accelerator), device


#: Кто выполняет текущий запрос. Переменная контекста, а не параметр тридцати
#: ручек: подстановку в одном месте нельзя забыть, а проверку в тридцати —
#: можно, и цена забывчивости здесь — чужие записи на экране.
CURRENT_USER: ContextVar[accounts.User | None] = ContextVar("narezka_user", default=None)


def _workspace(asked: str | None) -> str:
    """Пространство хранения запроса.

    При включённом входе — только своё, что бы ни просил клиент: параметр
    `project` тогда игнорируется, а не проверяется.
    """
    config, _ = _config()
    if config.auth.enabled:
        return (CURRENT_USER.get() or accounts.LOCAL_USER).workspace
    return asked or config.default_project


@app.middleware("http")
async def guard(request, call_next):
    """Запрет по умолчанию: доступ открыт списком, а не закрыт им."""
    if auth.is_public(request.url.path):
        return await call_next(request)

    config, _ = _config()
    user = auth.current_user(request, config)
    if user is None:
        return JSONResponse({"detail": "нужен вход"}, status_code=401)

    request.state.user = user
    token = CURRENT_USER.set(user)
    try:
        return await call_next(request)
    finally:
        CURRENT_USER.reset(token)


def _paths(video_id: str, project: str):
    config, _ = _config()
    project = _workspace(project)
    paths = video_paths(config.storage_root, project, video_id)
    if not paths.exists():
        raise HTTPException(status_code=404, detail=f"видео {video_id} не найдено")
    return paths, config


def _context(video_id: str, project: str, profile: str | None = None) -> StageContext:
    config, device = _config(profile)
    project = _workspace(project)
    paths = video_paths(config.storage_root, project, video_id)
    if not paths.exists():
        raise HTTPException(status_code=404, detail=f"видео {video_id} не найдено")
    return StageContext(
        project_id=project,
        video_id=video_id,
        paths=paths,
        config=config,
        device=device,
        log=log,
    )


def _stage_states(paths) -> list[dict[str, Any]]:
    states = []
    for stage in PIPELINE:
        state_file = Artifact(paths.stage_state / f"{stage.name}.json")
        entry: dict[str, Any] = {
            "name": stage.name,
            "description": stage.description,
            "device": stage.device.value,
            "optional": stage.optional,
            # Кусок работы, к которому стадия относится: интерфейс по нему
            # показывает ход той кнопки, которую человек нажал.
            "group": stage.group,
            "status": "pending",
        }
        if state_file.exists():
            try:
                data = state_file.read_json()
                entry.update(
                    status="done",
                    finished_at=data.get("finished_at"),
                    duration=data.get("duration_seconds"),
                )
            except ValueError:
                pass
        states.append(entry)
    return states


# --- служебное -------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    config, device = _config()
    checks, _ = env.run_checks(config.storage_root, config.device)
    return {
        "ok": all(c.ok for c in checks if c.critical),
        "profile": config.resolved_profile,
        "device": {"kind": device.kind, "name": device.name},
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail, "critical": c.critical} for c in checks],
    }


# --- вход -------------------------------------------------------------------


class Credentials(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    #: Код приглашения. Нужен при регистрации: код выдают одному человеку
    #: и можно не выдать другому, а открытая настройка такого не умеет.
    invite: str | None = Field(default=None, max_length=64)


def _set_session(response: JSONResponse, token: str, config) -> None:
    """Ключ сессии в куке: httponly, чтобы его не достал сторонний скрипт,
    samesite=lax — чтобы он не уходил по чужим ссылкам."""
    response.set_cookie(
        auth.COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=config.auth.secure_cookie,
        max_age=accounts.SESSION_DAYS * 86400,
        path="/",
    )


@app.get("/api/billing")
def billing(project: str = "default") -> dict[str, Any]:
    """Счёт, цены и последние движения.

    Цены отдаются вместе с балансом: «осталось 40 кредитов» ничего не
    значит, пока непонятно, на сколько часов записи этого хватит.
    """
    config, _ = _config()
    space = _workspace(project)
    with db.connect(config.storage_root) as connection:
        return {
            "enabled": config.billing.enabled,
            "balance": credits.balance(connection, space),
            "rates": {
                "version": config.billing.version,
                "per_video_hour": config.billing.per_video_hour,
                "minimum": config.billing.minimum,
            },
            "history": credits.history(connection, space, limit=30),
        }


@app.get("/api/auth/me")
def whoami(request: Request) -> dict[str, Any]:
    """Кто вошёл и нужен ли вход вообще.

    Отвечает всегда, а не 401: интерфейс по этому ответу решает, показать
    ему форму входа или рабочий экран.
    """
    config, _ = _config()
    return auth.describe(auth.current_user(request, config), config)


@app.post("/api/auth/login")
def login(payload: Credentials, request: Request) -> JSONResponse:
    config, _ = _config()
    if not config.auth.enabled:
        return JSONResponse(auth.describe(accounts.LOCAL_USER, config))

    wait = auth.too_many(request, payload.login)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"слишком много попыток, попробуйте через {wait // 60 + 1} мин",
        )

    with db.connect(config.storage_root) as connection:
        user = accounts.verify(connection, login=payload.login, password=payload.password)
        if user is None:
            auth.note_failure(request, payload.login)
            # Один ответ на «нет такого логина» и «неверный пароль»: разница
            # между ними — готовый список зарегистрированных.
            raise HTTPException(status_code=401, detail="неверный логин или пароль")
        token = accounts.open_session(connection, user)

    auth.note_success(request, payload.login)
    log.info("вход: %s", user.login)
    response = JSONResponse(auth.describe(user, config))
    _set_session(response, token, config)
    return response


@app.post("/api/auth/signup")
def signup(payload: Credentials) -> JSONResponse:
    config, _ = _config()
    if not config.auth.enabled or not config.auth.allow_signup:
        raise HTTPException(status_code=403, detail="регистрация закрыта")

    if not payload.invite:
        raise HTTPException(status_code=400, detail="нужен код приглашения")

    with db.connect(config.storage_root) as connection:
        try:
            user = accounts.create(connection, login=payload.login, password=payload.password)
            gift = accounts.take_invite(connection, payload.invite, user)
        except accounts.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Подарок при регистрации: без него человек входит и упирается
        # в «не хватает кредитов», не увидев, ради чего всё затевалось.
        bonus = gift or config.billing.signup_bonus
        if config.billing.enabled and bonus > 0:
            credits.add(
                connection, workspace=user.workspace, amount=bonus,
                kind="grant", note=f"приглашение {payload.invite}",
            )
        token = accounts.open_session(connection, user)

    log.info("заведена учётка %s, пространство %s", user.login, user.workspace)
    response = JSONResponse(auth.describe(user, config))
    _set_session(response, token, config)
    return response


@app.post("/api/auth/logout")
def logout(request: Request) -> JSONResponse:
    config, _ = _config()
    token = request.cookies.get(auth.COOKIE)
    if token and config.auth.enabled:
        with db.connect(config.storage_root) as connection:
            accounts.close_session(connection, token)
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


@app.get("/api/groups")
def groups() -> dict[str, Any]:
    """Куски работы и стадии, которые в каждый входят."""
    return {
        "groups": [
            {"name": name, "title": title, "stages": [s.name for s in stages_for(name)]}
            for name, title in GROUPS.items()
        ]
    }


@app.get("/api/stages")
def stages() -> list[dict[str, Any]]:
    return [
        {
            "name": s.name,
            "description": s.description,
            "device": s.device.value,
            "optional": s.optional,
            "group": s.group,
            "title": STAGE_TITLES.get(s.name, s.name),
        }
        for s in PIPELINE
    ]


# --- видео -----------------------------------------------------------------


class AddVideo(BaseModel):
    url: str | None = None
    file: str | None = None
    project: str = "default"
    #: Своё название записи. В интерфейсе она зовётся проектом, в коде и в
    #: хранилище — видео: переименовывать сущность ради слова на экране
    #: незачем, а помнить о расхождении надо (см. core/registry.py).
    title: str | None = Field(default=None, max_length=registry.TITLE_LIMIT)
    #: На каком основании берётся запись: «own» — своя, «permission» —
    #: есть разрешение правообладателя. На сервисе обязательно.
    rights: str | None = None


class RenameVideo(BaseModel):
    #: Пустая строка означает возврат к заголовку из источника.
    title: str = Field(default="", max_length=registry.TITLE_LIMIT)


def _pending(config, workspace: str) -> dict[str, dict[str, Any]]:
    """Что стоит в очереди у этого владельца — одним запросом на весь каталог.

    Запрашивать по записи значило бы восемь обращений к базе на каждый опрос
    каталога, а опрашивается он раз в три секунды.
    """
    with db.connect(config.storage_root) as connection:
        queue.ensure_schema(connection)
        rows = connection.execute(
            "SELECT task_id, video_id, status FROM tasks"
            " WHERE workspace = ? AND status IN ('queued','running')",
            (workspace,),
        ).fetchall()
        return {
            row["video_id"]: {
                "status": row["status"],
                "position": queue.position(connection, row["task_id"]),
            }
            for row in rows
        }


def _summary(
    config, project: str, video_id: str, pending: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Карточка каталога: что за запись, откуда и на чём остановилась работа."""
    paths = video_paths(config.storage_root, project, video_id)
    meta = Artifact(paths.metadata)
    data = meta.read_json() if meta.exists() else {}

    done = {p.stem for p in paths.stage_state.glob("*.json")} if paths.stage_state.is_dir() else set()
    stages_done = sum(1 for stage in PIPELINE if stage.name in done)

    shorts_index = Artifact(paths.shorts / "index.json")
    shorts_count = 0
    if shorts_index.exists():
        try:
            shorts_count = len(shorts_index.read_json().get("files", []))
        except ValueError:
            shorts_count = 0

    waiting = (pending or {}).get(video_id)
    job = jobs.manager.get(video_id, project)
    if waiting and waiting["status"] == "queued":
        # Ожидание — отдельное состояние: «идёт обработка» на записи, до
        # которой очередь ещё не дошла, было бы неправдой.
        state = "queued"
    elif job is not None and job.is_active:
        state = "processing"
    elif job is not None and job.status == "failed":
        # Упавший прогон важнее готовых роликов: о нём надо узнать из каталога,
        # а не открыв запись и не найдя ожидаемого.
        state = "failed"
    elif shorts_count:
        state = "ready"
    elif stages_done:
        state = "started"
    else:
        state = "draft"

    return {
        "video_id": video_id,
        "title": registry.display_title(data, video_id),
        # Название из источника показывается второй строкой, когда человек
        # дал записи своё имя: иначе непонятно, что это за запись вообще.
        "source_title": data.get("source_title") or data.get("source_file"),
        "named": bool(data.get("title")),
        "origin": data.get("origin", {}).get("type"),
        "created_at": data.get("created_at") or _added_at(paths),
        "duration_seconds": data.get("duration_seconds"),
        "video": data.get("video"),
        # None означает «ещё неизвестно»: проверка файла не выполнялась.
        # Карточке это нужно, чтобы объяснить отсутствие кадра честно —
        # «только звук» и «запись ещё не скачана» разные вещи.
        "has_video": data.get("has_video"),
        "state": state,
        "stages_done": stages_done,
        "stages_total": len(PIPELINE),
        "shorts": shorts_count,
        "poster_at": _poster_at(paths, data),
        "job_status": job.status if job else None,
        "job": job.progress() if job else None,
        # Место в очереди: 0 — уже выполняется, 1 — следующая на очереди.
        "queue_position": (waiting or {}).get("position", 0),
    }


def _added_at(paths) -> str | None:
    """Когда запись завели, если дата не записана.

    Видео, добавленные до появления поля, датируются по каталогу — «дата
    неизвестна» в карточке выглядит поломкой, хотя ничего не сломано.
    """
    try:
        stamp = paths.base.stat().st_mtime
    except OSError:
        return None
    from datetime import datetime, timezone  # noqa: PLC0415

    return datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds")


def _poster_at(paths, metadata: dict[str, Any]) -> float | None:
    """Момент, из которого брать кадр для карточки, или None.

    None значит «кадра не будет»: запись ещё не скачана или это звук без
    картинки. Решает сервер, а не браузер, — иначе карточка запрашивает кадр,
    получает ошибку и показывает битую картинку.
    """
    if not metadata.get("has_video", False):
        return None
    try:
        find_source(paths.source)
    except MediaError:
        return None
    # Округление держит имя файла кадра постоянным: карточка при каждом
    # опросе просит тот же кадр и получает его из кэша, а не гоняет ffmpeg.
    return round(_preview_position(paths, metadata), 2)


@app.get("/api/videos")
def videos(project: str = "default") -> list[dict[str, Any]]:
    config, _ = _config()
    space = _workspace(project)
    pending = _pending(config, space)
    return [
        _summary(config, space, vid, pending)
        for vid in list_videos(config.storage_root, space)
    ]


@app.post("/api/videos")
def add_video(payload: AddVideo) -> dict[str, Any]:
    config, _ = _config()
    try:
        result = registry.register(
            storage_root=config.storage_root,
            project=_workspace(payload.project),
            url=payload.url,
            file=Path(payload.file) if payload.file else None,
            title=payload.title,
            allowed_hosts=config.sources.allowed_hosts,
            # На сервере путь к файлу — это чтение его же диска чужими
            # руками. Разрешено только там, где входа нет вовсе, то есть
            # на своей машине.
            allow_local_paths=config.sources.allow_local_paths and not config.auth.enabled,
            rights=payload.rights,
            # На своей машине спрашивать человека о правах на его же записи
            # незачем; на сервисе это первый вопрос (docs/BACKLOG-legal.md).
            require_rights=config.auth.enabled,
        )
    except registry.RegistrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"не удалось привязать файл: {exc}") from exc

    log.info("добавлено видео %s (%s)", result.video_id, result.placement)
    return {
        "video_id": result.video_id,
        "created": result.created,
        "title": result.title,
        "placement": result.placement,
    }


@app.patch("/api/videos/{video_id}")
def rename_video(video_id: str, payload: RenameVideo, project: str = "default") -> dict[str, Any]:
    """Переименование записи. Пустое название возвращает заголовок источника."""
    paths, config = _paths(video_id, project)
    try:
        registry.rename(paths, payload.title)
    except registry.RegistrationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _summary(config, _workspace(project), video_id)


#: Запас сверх самого файла: звук, кадры для разметки и готовые ролики
#: занимают ещё примерно столько же, а диск на домашнем сервере общий.
UPLOAD_RESERVE_BYTES = 5 * 1024**3


@app.post("/api/videos/upload")
async def upload(
    request: Request,
    name: str = Query(description="Имя файла с расширением"),
    title: str | None = Query(default=None, max_length=registry.TITLE_LIMIT),
    rights: str | None = Query(default=None, description="own | permission"),
    project: str = "default",
) -> dict[str, Any]:
    """Приём записи файлом.

    Тело запроса — сам файл, без multipart. Причины две: multipart требует
    отдельной зависимости, а главное — он разбирает поток на части ради
    полей формы, которых здесь нет. Запись на 6.6 ГБ пишется на диск
    кусками по мере прихода и никогда не держится в памяти целиком.

    Имя файла берётся только ради расширения: по нему стадии понимают,
    видео это или звук. Всё остальное в имени отбрасывается — путь,
    пришедший от постороннего, не должен участвовать в построении пути
    на диске.
    """
    config, _ = _config()
    safe = Path(name).name
    suffix = Path(safe).suffix.lower()
    if not suffix:
        raise HTTPException(status_code=400, detail="у файла нет расширения")

    limit = int(config.sources.max_upload_gb * 1024**3)
    space = _workspace(project)
    incoming = config.storage_root / "uploads" / space
    incoming.mkdir(parents=True, exist_ok=True)

    # Место проверяется до приёма, а не в момент, когда оно кончится. Запись
    # на несколько гигабайт идёт через туннель десятки минут, и узнать об
    # отказе в конце — значит потратить их впустую. Запас нужен потому, что
    # на записи потом ещё работать: звук, кадры, готовые ролики.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit():
        need = int(declared) + UPLOAD_RESERVE_BYTES
        free = env.free_gb(config.storage_root) * 1024**3
        if need > free:
            raise HTTPException(
                status_code=507,
                detail=(
                    f"на диске свободно {free / 1024**3:.1f} ГБ, "
                    f"а нужно {need / 1024**3:.1f} ГБ вместе с запасом на обработку"
                ),
            )

    target = incoming / f"{uuid.uuid4().hex}{suffix}"
    written = 0
    try:
        with target.open("wb") as handle:
            async for chunk in request.stream():
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"файл больше {config.sources.max_upload_gb:g} ГБ",
                    )
                handle.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"не удалось принять файл: {exc}") from exc

    if written == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="пустой файл")

    try:
        result = registry.register(
            storage_root=config.storage_root,
            project=space,
            file=target,
            title=title,
            allow_local_paths=True,  # файл уже у нас, путь свой
            rights=rights,
            require_rights=config.auth.enabled,
        )
    except registry.RegistrationError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Принятый файл лежит в хранилище записи ссылкой или копией; временный
    # оригинал больше не нужен, а место он занимает настоящее.
    if result.placement in ("жёсткая ссылка", "копия"):
        target.unlink(missing_ok=True)

    log.info("принят файл %s (%.2f ГБ) → %s", safe, written / 1024**3, result.video_id)
    return {
        "video_id": result.video_id,
        "created": result.created,
        "title": result.title,
        "size_bytes": written,
    }


@app.get("/api/videos/{video_id}")
def video_detail(video_id: str, project: str = "default") -> dict[str, Any]:
    paths, _ = _paths(video_id, project)
    meta = Artifact(paths.metadata)
    cost = Artifact(paths.cost)
    space = _workspace(project)
    job = jobs.manager.get(video_id, space)
    with db.connect(_config()[0].storage_root) as connection:
        task = queue.active_for(connection, workspace=space, video_id=video_id)
        place = queue.position(connection, task.task_id) if task else 0
    return {
        "video_id": video_id,
        "project": project,
        "queue_position": place,
        "metadata": meta.read_json() if meta.exists() else {},
        "stages": _stage_states(paths),
        "cost": cost.read_json() if cost.exists() else None,
        "job": job.snapshot() if job else None,
    }


@app.delete("/api/videos/{video_id}")
def delete_video(video_id: str, project: str = "default") -> dict[str, str]:
    import shutil  # noqa: PLC0415

    paths, _ = _paths(video_id, project)
    job = jobs.manager.get(video_id, _workspace(project))
    if job and job.is_active:
        raise HTTPException(status_code=409, detail="видео обрабатывается — дождитесь завершения")
    shutil.rmtree(paths.base)
    return {"status": "deleted"}


# --- запуск обработки ------------------------------------------------------


class RunRequest(BaseModel):
    stage: str | None = None
    #: Кусок работы: `analysis`, `shorts` или `long`. Единой кнопки «сделать
    #: всё» нет намеренно — человек запускает то, что ему сейчас нужно,
    #: а недостающее подтягивается само (см. stages.stages_for).
    group: str | None = None
    force: bool = False
    profile: str | None = None
    project: str = "default"


@app.post("/api/videos/{video_id}/run")
def run(video_id: str, payload: RunRequest) -> dict[str, Any]:
    """Ставит работу в очередь.

    Не запускает сразу: одна расшифровка занимает машину на час с лишним,
    и две рядом не ускоряют обработку, а множат расход памяти. Очередь
    лежит в базе, поэтому переживает перезапуск сервера, а прерванная
    работа возобновляется почти бесплатно — стадии кэшируются.
    """
    ctx = _context(video_id, payload.project, payload.profile)

    if payload.group is not None:
        try:
            stages_for(payload.group)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.stage is not None:
        try:
            get_stage(payload.stage)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    config, _ = _config()
    quote = _quote(config, ctx, payload)
    with db.connect(config.storage_root) as connection:
        if config.billing.enabled:
            try:
                credits.ensure_enough(connection, workspace=ctx.project_id, quote=quote)
            except credits.NotEnoughCredits as exc:
                # 402 — «нужна оплата»: отдельный код, чтобы интерфейс мог
                # показать пополнение, а не общую ошибку.
                raise HTTPException(status_code=402, detail=str(exc)) from exc
        task, created = queue.enqueue(
            connection,
            workspace=ctx.project_id,
            video_id=video_id,
            work_group=payload.group,
            stage=payload.stage,
            force=payload.force,
        )
        if not created:
            _refuse_if_busy(task, work_group=payload.group, stage=payload.stage)
        place = queue.position(connection, task.task_id)

    job, _ = jobs.manager.reserve(video_id, ctx.project_id)
    log.info("в очередь: %s/%s, место %d", ctx.project_id, video_id, place)
    return {
        "started": created,
        "status": job.status,
        "queued": place > 0,
        "position": place,
        # «Не больше»: часть стадий возьмётся из кэша и не будет стоить
        # ничего. Списание никогда не превышает названного.
        "estimate": quote.credits if quote.known else None,
    }


def _describe_task(task) -> str:
    """Чем занята запись — словами, а не полями."""
    if task.clip_index is not None:
        return f"пересборка ролика {task.clip_index + 1}"
    if task.stage:
        return f"шаг «{STAGE_TITLES.get(task.stage, task.stage)}»"
    return {"analysis": "разбор записи", "shorts": "сборка роликов",
            "long": "длинная нарезка"}.get(task.work_group, "обработка")


def _refuse_if_busy(task, *, work_group=None, stage=None, clip_index=None) -> None:
    """Отказ, когда записью уже занята другая работа.

    Очередь намеренно не плодит задачи на одну запись: двойной клик не должен
    ставить две сборки. Но раньше она так же молча возвращала **чужую**
    задачу — нажатие «пересобрать ролик» во время разбора не делало ничего,
    а интерфейс показывал «в очереди». Совпадающая работа по-прежнему
    возвращается как есть, несовпадающая честно отказывает.
    """
    same = (
        task.work_group == work_group
        and task.stage == stage
        and task.clip_index == clip_index
    )
    if not same:
        raise HTTPException(
            status_code=409,
            detail=f"запись уже в очереди: {_describe_task(task)}. Дождитесь или остановите",
        )


def _quote(config, ctx, payload) -> credits.Quote:
    """Верхняя граница стоимости запуска.

    Считается по всем кускам работы, которые могут выполниться, — то есть
    как если бы кэша не было. Реально спишется меньше или столько же.
    """
    meta = Artifact(ctx.paths.metadata)
    duration = None
    if meta.exists():
        try:
            duration = meta.read_json().get("duration_seconds")
        except ValueError:
            duration = None

    if payload.stage:
        groups = [get_stage(payload.stage).group]
    elif payload.group:
        groups = sorted({stage.group for stage in stages_for(payload.group)})
    else:
        groups = sorted({stage.group for stage in PIPELINE})
    return credits.quote_for(config.billing, groups, duration)


@app.get("/api/videos/{video_id}/timeline")
def timeline(video_id: str, buckets: int = Query(600, ge=60, le=2000)) -> dict[str, Any]:
    """Данные для полосы VOD: чат по корзинам и найденные моменты.

    Корзины считаются на сервере: слать браузеру 37 тысяч сообщений, чтобы он
    сам их сгруппировал, — это мегабайты ради шестисот чисел.
    """
    ctx = _context(video_id, "default", None)
    meta = Artifact(ctx.paths.metadata)
    duration = (meta.read_json().get("duration_seconds") if meta.exists() else None) or 0.0

    chat_art = Artifact(ctx.paths.analysis / "chat.json")
    chat: list[float] = []
    if chat_art.exists() and duration > 0:
        counts = [0] * buckets
        for message in chat_art.read_json().get("messages", []):
            index = int(message["at"] / duration * buckets)
            if 0 <= index < buckets:
                counts[index] += 1
        peak = max(counts) or 1
        # Нормируется к пику: полоса показывает форму всплесков, а не
        # абсолютные числа — их всё равно не прочесть на 44 пикселях.
        chat = [round(c / peak, 3) for c in counts]

    moments: list[dict[str, Any]] = []
    try:
        clips, _ = load_clips(ctx.paths, apply_review=False)
    except (FileNotFoundError, ValueError):
        # Отбора может ещё не быть — полоса тогда рисует только чат.
        # Перехват узкий намеренно: широкий скрыл отсутствующий импорт,
        # и стадия молча возвращала ноль моментов вместо ошибки.
        clips = []
    for clip in clips:
        moments.append({
            "index": clip.get("index"),
            "start": clip.get("start"),
            "end": clip.get("end"),
            "score": clip.get("interest_score") or clip.get("provisional_score") or 0.0,
            "selected": bool(clip.get("selected", True)),
        })

    return {"duration": duration, "buckets": buckets, "chat": chat, "moments": moments}


class PiecesPayload(BaseModel):
    """Правленые границы кусков одной нарезки."""

    file: str
    pieces: list[dict[str, float]]


@app.put("/api/videos/{video_id}/compilation/pieces")
def set_pieces(video_id: str, payload: PiecesPayload, project: str = "default") -> dict[str, Any]:
    """Сохраняет правку границ поверх расчёта, не затирая его.

    Расчётный план остаётся на месте: правка должна быть обратимой, а
    пересчёт — возможным. Тот же приём, что у настроек кадрирования.
    """
    ctx = _context(video_id, project, None)
    cleaned = []
    for item in payload.pieces:
        start, end = float(item.get("start", 0)), float(item.get("end", 0))
        if end - start > 0.5:
            cleaned.append({"start": round(start, 3), "end": round(end, 3)})
    if not cleaned:
        raise HTTPException(status_code=400, detail="ни одного куска длиннее полусекунды")

    cleaned.sort(key=lambda p: p["start"])
    artifact = Artifact(ctx.paths.analysis / "compilation-edits.json")
    stored = artifact.read_json() if artifact.exists() else {}
    stored[payload.file] = cleaned
    artifact.write_json(stored)
    total = sum(p["end"] - p["start"] for p in cleaned)
    return {"file": payload.file, "pieces": len(cleaned), "duration": round(total, 2)}


@app.delete("/api/videos/{video_id}/compilation/pieces")
def reset_pieces(video_id: str, file: str, project: str = "default") -> dict[str, Any]:
    """Возвращает расчётные границы: правка стирается, план не трогался."""
    ctx = _context(video_id, project, None)
    artifact = Artifact(ctx.paths.analysis / "compilation-edits.json")
    if artifact.exists():
        stored = artifact.read_json()
        stored.pop(file, None)
        artifact.write_json(stored)
    return {"file": file, "restored": True}


@app.get("/api/videos/{video_id}/compilations")
def compilations(video_id: str, project: str = "default") -> dict[str, Any]:
    """Собранные длинные нарезки с описанием, из чего они сделаны."""
    ctx = _context(video_id, project, None)
    plan = Artifact(ctx.paths.analysis / "compilation.json")
    if not plan.exists():
        return {"files": [], "reason": "длинные нарезки ещё не собирались"}
    try:
        results = plan.read_json().get("results", [])
    except ValueError:
        return {"files": [], "reason": "план нарезок не читается"}

    files = []
    for item in results:
        path = ctx.paths.base / item.get("file", "")
        if not path.is_file():
            continue
        episode = item.get("episode") or {}
        files.append({
            "file": item["file"],
            "kind": "story" if episode else "best",
            "title": episode.get("title") or "Подборка лучших моментов",
            "summary": episode.get("summary", ""),
            "duration": item.get("duration", 0.0),
            "pieces": len(item.get("pieces", [])),
            "size_bytes": path.stat().st_size,
            "chapters_text": item.get("chapters_text", ""),
        })
    return {"files": files}


@app.get("/api/videos/{video_id}/compilations/{name}/media")
def compilation_media(
    video_id: str,
    name: str,
    project: str = "default",
    range_header: Annotated[str | None, Header(alias="range")] = None,
):
    """Отдаёт файл длинной нарезки.

    Имя сверяется со списком собранных, а не подставляется в путь как есть:
    иначе через него можно было бы дотянуться до любого файла на диске (§66).
    """
    ctx = _context(video_id, project, None)
    known = {item["file"] for item in compilations(video_id, project).get("files", [])}
    if name not in known:
        raise HTTPException(status_code=404, detail=f"нет нарезки {name}")
    return serve_file(ctx.paths.base / name, range_header)


def _episodes_payload(ctx) -> dict[str, Any]:
    """Найденные эпизоды и выбор человека по этой записи."""
    cfg = settings.compilation(ctx.config, ctx.paths)
    found: list[dict[str, Any]] = []
    reason = None

    artifact = Artifact(ctx.paths.analysis / "episodes.json")
    if not artifact.exists():
        reason = "эпизоды ещё не размечены"
    else:
        try:
            found = artifact.read_json().get("episodes", [])
        except ValueError:
            reason = "файл эпизодов не читается"

    payload = {
        "episodes": found,
        "selected": cfg.episodes,
        "story": cfg.story,
        "best": cfg.best,
        "target_minutes": cfg.target_minutes,
    }
    # Настройки отдаются всегда, даже когда эпизодов ещё нет: на этой вкладке
    # человек их и задаёт — до того, как что-то посчитано.
    return payload if reason is None else {**payload, "reason": reason}


@app.get("/api/videos/{video_id}/episodes")
def episodes(video_id: str, project: str = "default") -> dict[str, Any]:
    """Найденные эпизоды — чтобы человек выбрал, из каких делать ролики."""
    return _episodes_payload(_context(video_id, project, None))


class CompilationSettings(BaseModel):
    """Что собирать в длинную нарезку. Правка для одной записи."""

    story: bool = False
    best: bool = False
    #: Номера выбранных эпизодов; пустой список — ни одного.
    episodes: list[int] | None = None
    target_minutes: int = Field(default=20, ge=1, le=180)


@app.put("/api/videos/{video_id}/compilation")
def set_compilation(
    video_id: str, payload: CompilationSettings, project: str = "default"
) -> dict[str, Any]:
    """Сохраняет выбор по длинной нарезке для этой записи.

    Выбор человека и есть включение стадии: `enabled` не отдельная галочка,
    а следствие того, что хоть что-то выбрано. Отдельный переключатель
    «собирать длинную нарезку» поверх «собрать подборку» был бы вторым
    выключателем к той же лампе.

    Поиск эпизодов включается только под сюжетный режим: он стоит запросов
    к модели, и платить за него ради подборки лучших незачем.
    """
    ctx = _context(video_id, project, None)
    settings.update(
        ctx.paths,
        {
            settings.COMPILATION_KEY: {
                "story": payload.story,
                "best": payload.best,
                "episodes": payload.episodes,
                "target_minutes": payload.target_minutes,
                "enabled": payload.story or payload.best,
                "find_episodes": payload.story,
            }
        },
    )
    return _episodes_payload(ctx)


@app.get("/api/settings/subtitles")
def subtitle_presets() -> dict[str, Any]:
    """Готовые наборы субтитров и то, из чего можно собрать свой."""
    return {
        "presets": [
            {
                **preset,
                # Цвета наружу в привычном виде: поле выбора цвета в браузере
                # принимает #RRGGBB, а в ASS порядок байтов обратный.
                "colours": {
                    "primary": subs.hex_colour(preset["style"]["primary"]),
                    "highlight": subs.hex_colour(preset["style"]["highlight"]),
                    "outline_colour": subs.hex_colour(preset["style"]["outline_colour"]),
                },
            }
            for preset in subs.describe_presets()
        ],
        # Порядок как в поставке, а не по алфавиту: сверху то, чем режут
        # чаще всего, и рядом строка о том, чем шрифт отличается.
        "fonts": [
            {"name": name, "note": fonts.FONT_NOTES.get(name, "")}
            for name in fonts.FONT_FILES
            if fonts.is_vendored(name)
        ],
        "positions": [
            {"name": "bottom", "title": "Снизу"},
            {"name": "middle", "title": "По центру"},
            {"name": "top", "title": "Сверху"},
        ],
        "face_zoom": framing_core.face_zoom_presets(),
    }


class SubtitleSettings(BaseModel):
    """Оформление субтитров для одной записи."""

    preset: str = subs.DEFAULT_PRESET
    font: str | None = None
    font_size: int | None = Field(default=None, ge=24, le=140)
    #: Цвета приходят как #RRGGBB — так их отдаёт поле выбора в браузере.
    primary: str | None = None
    highlight: str | None = None
    outline_colour: str | None = None
    outline: float | None = Field(default=None, ge=0, le=10)
    bold: bool | None = None
    position: Literal["bottom", "middle", "top"] | None = None
    max_words_per_line: int | None = Field(default=None, ge=1, le=12)
    max_chars_per_line: int | None = Field(default=None, ge=8, le=60)
    max_lines: int | None = Field(default=None, ge=1, le=4)


def _subtitle_style_fields(payload: SubtitleSettings) -> dict[str, Any]:
    """Правки стиля из того, что прислал интерфейс.

    Цвета переводятся в формат ASS здесь, а не в ядре: ядро не должно знать,
    что где-то есть поле выбора цвета в браузере.
    """
    fields: dict[str, Any] = {}
    for name in ("font", "font_size", "outline", "bold", "position",
                 "max_words_per_line", "max_chars_per_line", "max_lines"):
        value = getattr(payload, name)
        if value is not None:
            fields[name] = value
    for name in ("primary", "highlight", "outline_colour"):
        value = getattr(payload, name)
        if value:
            try:
                fields[name] = subs.ass_colour(value)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
    return fields


def _subtitles_payload(config, paths) -> dict[str, Any]:
    style = settings.subtitles(config, paths)
    stored = settings.load(paths).get(settings.SUBTITLES_KEY) or {}
    return {
        "preset": stored.get("preset") or config.subtitles.style,
        "custom": bool(stored.get("style")),
        "style": {
            **style.__dict__,
            "primary_hex": subs.hex_colour(style.primary),
            "highlight_hex": subs.hex_colour(style.highlight),
            "outline_hex": subs.hex_colour(style.outline_colour),
        },
    }


@app.get("/api/videos/{video_id}/subtitles")
def subtitles_settings(video_id: str, project: str = "default") -> dict[str, Any]:
    paths, config = _paths(video_id, project)
    return _subtitles_payload(config, paths)


@app.put("/api/videos/{video_id}/subtitles")
def set_subtitles(
    video_id: str, payload: SubtitleSettings, project: str = "default"
) -> dict[str, Any]:
    """Сохраняет оформление субтитров для записи.

    Пресет и правки хранятся отдельно: смена набора не должна стирать
    выбранный цвет, а сброс правок — возвращать к набору, а не к пустоте.
    """
    paths, config = _paths(video_id, project)
    try:
        subs.preset_style(payload.preset)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    settings.update(
        paths,
        {settings.SUBTITLES_KEY: {
            "preset": payload.preset,
            "style": _subtitle_style_fields(payload),
        }},
    )
    log.info("видео %s: субтитры «%s»", video_id, payload.preset)
    return _subtitles_payload(config, paths)


@app.delete("/api/videos/{video_id}/subtitles")
def reset_subtitles(video_id: str, project: str = "default") -> dict[str, Any]:
    """Возвращает оформление к набору из конфига."""
    paths, config = _paths(video_id, project)
    settings.update(paths, {settings.SUBTITLES_KEY: {}})
    return _subtitles_payload(config, paths)


@app.get("/api/settings/encoders")
def encoders_info() -> dict[str, Any]:
    """Чем сжимать видео — с плюсами и минусами каждого варианта."""
    from narezka.core import encoders as enc  # noqa: PLC0415

    return {"selected": load_config().output.encoder, "encoders": enc.describe_encoders()}


@app.post("/api/videos/{video_id}/stop")
def stop(video_id: str, project: str = "default") -> dict[str, Any]:
    """Снимает задачу: из очереди — сразу, начатую — после текущей стадии."""
    config, _ = _config()
    space = _workspace(project)

    with db.connect(config.storage_root) as connection:
        task = queue.active_for(connection, workspace=space, video_id=video_id)
        cancelled = bool(task) and queue.cancel(connection, task.task_id)

    if cancelled:
        # Снятая из очереди задача закрывается и в памяти, иначе она вечно
        # числится живой, а поток событий ждёт того, чего не будет.
        waiting_job = jobs.manager.get(video_id, space)
        if waiting_job is not None:
            waiting_job.cancel()

    stopped = jobs.manager.stop(video_id, space)
    if not cancelled and not stopped:
        raise HTTPException(status_code=409, detail="обработка не идёт — останавливать нечего")
    return {"stopping": True, "from_queue": cancelled}


@app.get("/api/videos/{video_id}/events")
async def events(
    video_id: str, since: int = Query(0), project: str = "default"
) -> StreamingResponse:
    """Поток событий обработки (§69: прогресс в реальном времени)."""
    # Пространство берётся до потока: внутри генератора запрос уже завершён,
    # и переменная контекста там пуста.
    workspace = _workspace(project)

    async def stream():
        cursor = since
        idle_ticks = 0
        while True:
            job = jobs.manager.get(video_id, workspace)
            if job is None:
                yield f"data: {json.dumps({'event': 'no_job'})}\n\n"
                return

            batch = await asyncio.to_thread(job.events_since, cursor, timeout=15.0)
            for event in batch:
                cursor = event.seq
                yield f"data: {json.dumps(event.as_dict(), ensure_ascii=False)}\n\n"

            if not batch:
                idle_ticks += 1
                # Пульс держит соединение живым через прокси.
                yield ": ping\n\n"
                if not job.is_active and idle_ticks >= 2:
                    return
            else:
                idle_ticks = 0

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- артефакты -------------------------------------------------------------


@app.get("/api/videos/{video_id}/transcript")
def transcript(video_id: str, project: str = "default", include_suspect: bool = True) -> dict[str, Any]:
    paths, _ = _paths(video_id, project)
    artifact = Artifact(paths.transcript / "transcript.json")
    if not artifact.exists():
        raise HTTPException(status_code=404, detail="транскрипт ещё не готов")
    data = artifact.read_json()
    if not include_suspect:
        data["segments"] = [s for s in data.get("segments", []) if not s.get("suspect")]
    return data


@app.get("/api/videos/{video_id}/candidates")
def candidates(video_id: str, project: str = "default") -> dict[str, Any]:
    paths, _ = _paths(video_id, project)
    artifact = Artifact(paths.analysis / "candidates.json")
    if not artifact.exists():
        raise HTTPException(status_code=404, detail="кандидаты ещё не отобраны")
    return artifact.read_json()


@app.get("/api/videos/{video_id}/shorts")
def shorts(video_id: str, project: str = "default") -> dict[str, Any]:
    paths, _ = _paths(video_id, project)
    artifact = Artifact(paths.shorts / "index.json")
    if not artifact.exists():
        raise HTTPException(status_code=404, detail="ролики ещё не отрендерены")
    return artifact.read_json()


@app.post("/api/videos/{video_id}/shorts/{index}/render")
def rerender_short(video_id: str, index: int, project: str = "default") -> dict[str, Any]:
    """Пересобирает один готовый ролик, не трогая остальные.

    Полная пересборка тридцати роликов — десятки минут, а поправить обычно
    надо один: у него не сработала детекция лица или подвинуты границы.
    Идёт через ту же очередь, что и остальная работа: две дороги к ffmpeg
    означали бы два запуска кодирования разом на машине, где память
    кончалась трижды.
    """
    ctx = _context(video_id, project, None)
    config, _ = _config()

    with db.connect(config.storage_root) as connection:
        task, created = queue.enqueue(
            connection,
            workspace=ctx.project_id,
            video_id=video_id,
            stage="render",
            clip_index=index,
        )
        if not created:
            _refuse_if_busy(task, stage="render", clip_index=index)
        place = queue.position(connection, task.task_id)

    job, _ = jobs.manager.reserve(video_id, ctx.project_id)
    log.info("в очередь: пересборка ролика %d записи %s", index, video_id)
    return {"started": created, "status": job.status, "position": place}


@app.get("/api/videos/{video_id}/shorts/{index}/media")
def short_media(
    video_id: str,
    index: int,
    project: str = "default",
    range_header: Annotated[str | None, Header(alias="range")] = None,
):
    paths, _ = _paths(video_id, project)
    # Имя строится из индекса, а не берётся из запроса: путь не должен
    # собираться из пользовательского ввода (§66).
    return serve_file(paths.shorts / f"{index:02d}.mp4", range_header)


#: Высота картинки предпросмотра по умолчанию. С запасом под экраны с двойной
#: плотностью: карточка в интерфейсе около 400 px, и кадр ровно в её размер
#: выглядит мылом.
PREVIEW_HEIGHT = 960

#: Пределы высоты. Ниже 240 разобрать оформление уже нельзя, выше 1440 кадр
#: считается заметно дольше, а разницы на экране нет. Высота — это скорость:
#: на слабой машине мелкий кадр появляется втрое быстрее.
PREVIEW_MIN_HEIGHT = 240
PREVIEW_MAX_HEIGHT = 1440


class FramingPayload(FramingConfig):
    """Настройки ролика, присланные из интерфейса.

    Кадрирование наследует проверки диапазонов у конфига, а переключатели
    того, что вшивать, живут здесь же: для пользователя это одна панель
    настроек, и разносить её по двум файлам незачем.
    """

    subtitles_enabled: bool = True
    loudnorm_enabled: bool = True
    chat_ignore_start: bool = True
    #: Модель и бэкенд зрения задаются на видео: так можно сравнить их
    #: на одном материале, а не менять глобально и терять сравнимость.
    llm_model: str | None = None
    detector_backend: str | None = None
    encoder: str | None = None
    #: Сигналы анализа — каждый отключается отдельно (§43).
    use_loudness: bool | None = None
    use_speech_rate: bool | None = None
    use_chat: bool | None = None
    use_chat_reactions: bool | None = None
    #: Теги звука — каждый отдельно (§43).
    tag_laughter: bool | None = None
    tag_music: bool | None = None
    tag_shout: bool | None = None
    tag_applause: bool | None = None
    tag_crowd: bool | None = None
    #: Слежение за лицом: насколько цепко рамка держит голову.
    track_smoothing: float | None = None
    track_dead_zone: float | None = None


#: Списки берутся из описания правок, а не переписываются здесь: набор
#: переключателей на экране и набор, который доходит до стадий, обязаны
#: совпадать — иначе снова появится настройка, которая ничего не делает.
SIGNAL_FLAGS = tuple(key for key in settings.SECTIONS["candidates"] if key.startswith("use_"))
AUDIO_TAGS = tuple(settings.SECTIONS["audiotags"].values())


def _framing_state(video_id: str, project: str) -> tuple[Any, Any, Framing, int, int]:
    paths, _ = _paths(video_id, project)
    ctx = _context(video_id, project)
    metadata_artifact = Artifact(paths.metadata)
    metadata = metadata_artifact.read_json() if metadata_artifact.exists() else {}
    width, height = source_size(metadata)
    return paths, ctx, load_framing(ctx), width, height


@app.get("/api/videos/{video_id}/framing")
def framing(video_id: str, project: str = "default") -> dict[str, Any]:
    """Текущее кадрирование и что дадут готовые варианты на этом исходнике."""
    paths, ctx, current, src_w, src_h = _framing_state(video_id, project)
    short = ctx.config.output.short
    plan = plan_frame(src_w, src_h, short.width, short.height, current)

    # Сплит возможен только там, где вебка найдена наложением: предлагать
    # раскладку, для которой нет данных, значит обещать несбыточное.
    facecam = Artifact(paths.analysis / "facecam.json")
    split_available = False
    if facecam.exists():
        try:
            split_available = any(
                not entry.get("full_frame")
                for entry in facecam.read_json().get("clips", {}).values()
            )
        except ValueError:
            split_available = False

    # Значения берутся из конфига контекста: правки для записи в нём уже
    # учтены. Отдельного слияния здесь нет намеренно — пока оно было своим,
    # экран показывал применённой настройку, до которой стадии не доходили.
    options = {
        **load_options(ctx),
        "llm_model": ctx.config.llm.model,
        "detector_backend": ctx.config.detector.backend,
        "encoder": ctx.config.output.encoder,
        "chat_ignore_start": ctx.config.candidates.chat_ignore_start,
        **{flag: getattr(ctx.config.candidates, flag) for flag in SIGNAL_FLAGS},
        **{f"tag_{tag}": getattr(ctx.config.audiotags, tag) for tag in AUDIO_TAGS},
        **{knob: getattr(ctx.config.output.framing, knob) for knob in ("track_smoothing", "track_dead_zone")},
    }
    stored = settings.load(paths)
    return {
        "current": {**current.__dict__, **options},
        "split_available": split_available,
        "custom": bool(set(stored) - {settings.COMPILATION_KEY, settings.SUBTITLES_KEY}),
        "source": {"width": src_w, "height": src_h},
        "output": {"width": short.width, "height": short.height},
        "plan": {
            "content_share": round(plan.content_share, 4),
            "lost_share": round(plan.lost_share, 4),
            "full_bleed": plan.full_bleed,
            "summary": describe(plan),
        },
        "presets": preview_presets(src_w, src_h, short.width, short.height),
    }


@app.put("/api/videos/{video_id}/framing")
def set_framing(video_id: str, payload: FramingPayload, project: str = "default") -> dict[str, Any]:
    paths, config = _paths(video_id, project)
    patch = payload.model_dump()
    try:
        # Проверка при сохранении, а не при чтении: негодное значение иначе
        # ляжет на диск и сломает запись позже — в момент запуска работы.
        settings.apply(config, {**settings.load(paths), **patch})
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    # Настройки записи лежат в одном файле — кадрирование, субтитры, длинная
    # нарезка. Запись целиком уносила чужие: правится только своё.
    settings.update(paths, patch)
    # Кадрирование входит в ключ кэша стадии render, поэтому отдельно ничего
    # сбрасывать не нужно: следующий запуск увидит другой ключ и перерендерит.
    log.info("видео %s: кадрирование «%s»", video_id, payload.preset)
    return framing(video_id, project)


@app.delete("/api/videos/{video_id}/framing")
def reset_framing(video_id: str, project: str = "default") -> dict[str, Any]:
    paths, _ = _paths(video_id, project)
    settings.reset(paths, set(FramingPayload.model_fields))
    return framing(video_id, project)


@app.get("/api/videos/{video_id}/framing/preview")
def framing_preview(
    request: Request,
    video_id: str,
    project: str = "default",
    at: float | None = Query(default=None, ge=0),
    height: int = Query(
        default=PREVIEW_HEIGHT, ge=PREVIEW_MIN_HEIGHT, le=PREVIEW_MAX_HEIGHT,
        description="Высота кадра: меньше — быстрее",
    ),
    short: int | None = Query(default=None, ge=0, description="Номер готового ролика"),
    offset: float | None = Query(default=None, ge=0, description="Секунда внутри ролика"),
):
    """Кадр ролика с текущими настройками — чтобы настраивать глазами.

    Один на всё: рамка, раскладка, субтитры. Двух предпросмотров быть не
    должно — человек настраивает один кадр, а не два разных.

    Любое поле кадрирования принимается правкой прямо в запросе, поэтому
    результат виден до нажатия «Запомнить кадр». Список полей берётся из
    самого конфига, а не переписывается здесь: пока он был переписан,
    половина настроек — раскладка, приближение лица, врезка — на кадр
    не влияла, и увидеть их можно было только после сохранения.

    Полный рендер ради проверки геометрии занимает минуты, один кадр —
    доли секунды.
    """
    paths, ctx, current, src_w, src_h = _framing_state(video_id, project)

    overrides = {
        key: value
        for key, value in request.query_params.items()
        if key in FramingConfig.model_fields and value != ""
    }
    try:
        requested = Framing(**FramingConfig(**{**current.__dict__, **overrides}).model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        source = find_source(paths.source)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    metadata_artifact = Artifact(paths.metadata)
    metadata = metadata_artifact.read_json() if metadata_artifact.exists() else {}
    if not metadata.get("has_video", True):
        raise HTTPException(status_code=409, detail="в источнике нет картинки")

    # Место в готовом ролике важнее прямого времени записи: человек ставит
    # паузу на нужном кадре и правит настройки, глядя именно на него.
    position = None
    if short is not None and offset is not None:
        position = _short_moment(paths, short, offset)
    if position is None:
        position = at if at is not None else _preview_position(paths, metadata)
    output = ctx.config.output.short
    plan = plan_frame(src_w, src_h, output.width, output.height, requested)

    # Субтитры рисуются в этом же кадре, а не в своём отдельном. Их
    # оформление зависит от раскладки — в сплите строка ложится на нижнюю
    # полосу, — а раскладка от них не зависит вовсе, и разглядывать это
    # порознь было нечестно: два кадра показывали два разных ролика.
    style = settings.subtitles(ctx.config, paths)
    # Переключатель субтитров живёт не в кадрировании, а рядом с ним — в том
    # же наборе настроек ролика. Правка из запроса учитывается так же, как
    # для рамки: снял галочку — кадр сразу без текста.
    asked = request.query_params.get("subtitles_enabled")
    wants_subtitles = (
        asked.lower() not in ("false", "0", "")
        if asked is not None
        else load_options(ctx)["subtitles_enabled"]
    )
    words = _preview_words(paths, position) if wants_subtitles else []
    ass_file = None
    if words:
        ass = subs.build_ass(
            words, style=style, width=output.width, height=output.height,
            time_offset=position,
        )
        ass_slug = hashlib.sha256(
            json.dumps(
                {**style.__dict__, "at": round(position, 2)},
                sort_keys=True, ensure_ascii=False,
            ).encode()
        ).hexdigest()[:12]
        ass_file = (paths.base / "meta" / f"preview-{ass_slug}.ass").resolve()
        ass_file.write_text(ass, encoding="utf-8")

    # Имя от параметров: несколько вкладок с разными настройками не затрут
    # предпросмотр друг друга. Оформление субтитров входит наравне с рамкой —
    # иначе смена цвета возвращала бы прежний кадр из кэша.
    slug = hashlib.sha256(
        json.dumps(
            {
                **requested.__dict__,
                **({"subtitles": style.__dict__} if ass_file else {}),
                "at": round(position, 2),
                "height": height,
            },
            sort_keys=True, ensure_ascii=False,
        ).encode()
    ).hexdigest()[:12]
    target = paths.base / "meta" / f"preview-{slug}.jpg"

    # Предпросмотр смотрят в браузере на небольшой карточке — полный размер
    # 1080x1920 тут не нужен. Уменьшение встраивается в саму цепочку: -vf
    # нельзя применить к потоку, который уже пришёл из -filter_complex.
    chain = build_layout_filter(
        requested, plan, output.width, output.height,
        **_preview_layout(paths, requested, src_w, src_h, output, at=position),
        subtitle_name=fonts.escape_for_filter(ass_file) if ass_file else None,
        fonts_dir=fonts.escape_for_filter(fonts.fonts_dir()) if ass_file else None,
    )
    chain = chain.removesuffix("[v]") + f",scale=-2:{height}[v]"

    if not target.exists():
        try:
            run_tool(
                [
                    "ffmpeg", "-nostdin", "-v", "error", "-y",
                    "-ss", f"{position:.3f}",
                    "-i", str(source.resolve()),
                    "-frames:v", "1",
                    "-filter_complex", chain,
                    "-map", "[v]",
                    "-q:v", "4",
                    "-f", "mjpeg",
                    str(target),
                ],
                timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    _trim_previews(paths)
    _trim_previews_of(paths, "preview-*.ass", PREVIEW_KEEP)
    return serve_file(target, None)


def _preview_words(paths, at: float, span: float = 6.0) -> list[dict[str, Any]]:
    """Слова вокруг момента — чтобы в кадре был настоящий текст записи.

    Придуманная строка «Пример субтитров» показала бы шрифт, но не показала
    бы главного: как длина реальных фраз ложится в заданное число слов.
    """
    artifact = Artifact(paths.transcript / "transcript.json")
    if not artifact.exists():
        return []
    try:
        segments = artifact.read_json().get("segments", [])
    except ValueError:
        return []
    return subs.words_in_range(segments, at - span, at + span)


def _facecam_clips(paths) -> dict[str, Any]:
    facecam = Artifact(paths.analysis / "facecam.json")
    if not facecam.exists():
        return {}
    try:
        return facecam.read_json().get("clips", {}) or {}
    except ValueError:
        return {}


def _split_moment(paths) -> float | None:
    """Момент внутри клипа, где вебка найдена наложением.

    Окно вебки ищется **по клипу**, а не раз на запись: замер показал, что
    стрим переключается между полноэкранной камерой и демонстрацией экрана.
    Поэтому кадр для предпросмотра надо брать из того же клипа, откуда взята
    рамка, иначе в верхней полосе оказывается не лицо, а случайный угол.
    Ровно это и вышло на первой проверке.
    """
    overlays = {
        index for index, item in _facecam_clips(paths).items()
        if item and not item.get("full_frame")
    }
    if not overlays:
        return None
    try:
        clips, _ = load_clips(paths, apply_review=False)
    except (FileNotFoundError, ValueError):
        return None
    for clip in clips:
        if str(clip.get("index")) in overlays:
            return float(clip["start"]) + min(2.0, (clip["end"] - clip["start"]) / 2)
    return None


def _preview_cam(paths, at: float | None = None):
    """Найденная вебка для показываемого кадра.

    Окно вебки ищется **по клипу**, а не раз на запись: стрим переключается
    между полноэкранной камерой и демонстрацией экрана. Рамка не из того
    клипа даёт в полосе не лицо, а случайный угол — ровно это и вышло
    на первой проверке.
    """
    overlays = {
        index: item for index, item in _facecam_clips(paths).items()
        if item and not item.get("full_frame")
    }
    if not overlays:
        return None
    if at is not None:
        try:
            found, _ = load_clips(paths, apply_review=False)
        except (FileNotFoundError, ValueError):
            found = []
        for clip in found:
            if clip["start"] <= at <= clip["end"] and str(clip.get("index")) in overlays:
                return overlays[str(clip["index"])]
    return next(iter(overlays.values()))


def _rect(entry, key: str):
    found = (entry or {}).get(key) or {}
    try:
        return (found["x"], found["y"], found["width"], found["height"])
    except KeyError:
        return None


def _preview_layout(paths, current, src_w, src_h, short, at: float | None = None) -> dict[str, Any]:
    """Данные раскладки для предпросмотра — те же, что рендер считает клипу.

    Возвращается набором для `build_layout_filter`, поэтому предпросмотр и
    сборка идут одной дорогой. Пустой набор — раскладка к записи неприменима,
    и кадр строится обычным способом.
    """
    if current.layout == "single":
        return {}

    cam = _preview_cam(paths, at)
    if current.layout == "track":
        # Слежение — движение, и на одном кадре его не показать. Показывается
        # то, что попадает в рамку: её ширина и положение головы.
        return {
            "camera": plan_track_still(
                src_w, src_h, short.width, short.height, _rect(cam, "face")
            )
        }
    if cam is None:
        return {}

    window = (cam["x"], cam["y"], cam["width"], cam["height"])
    face = _rect(cam, "face")
    if current.layout == "split":
        plan = plan_split(
            src_w, src_h, short.width, short.height, window,
            face=face,
            content=_rect(cam, "content"),
            anchor=current.anchor,
            top_share=current.split_top_share,
            face_zoom=current.face_zoom,
            face_vertical=current.face_vertical,
        )
        return {"split": plan} if plan is not None else {}
    if current.layout == "camera":
        crop = plan_camera(
            src_w, src_h, short.width, short.height, window,
            face=face, zoom=current.face_zoom, vertical=current.face_vertical,
        )
        return {"camera": crop} if crop is not None else {}

    crop = plan_pip(
        src_w, src_h, window,
        face=face, zoom=current.face_zoom, vertical=current.face_vertical,
    )
    return {"pip": crop} if crop is not None else {}


#: Сколько картинок предпросмотра держать на видео. Они служат кэшем — при
#: возврате к уже опробованной настройке кадр появляется мгновенно, — но расти
#: без предела не должны.
PREVIEW_KEEP = 20


def _trim_previews(paths) -> None:
    files = sorted(
        (paths.base / "meta").glob("preview-*.jpg"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in files[PREVIEW_KEEP:]:
        stale.unlink(missing_ok=True)


def _short_moment(paths, index: int, offset: float) -> float | None:
    """Момент записи, который виден на `offset` секунде готового ролика.

    Нужно для настройки по остановленному кадру: человек смотрит ролик,
    ставит паузу на нужном месте и правит рамку — предпросмотр обязан
    показывать то же самое место, а не середину отрезка.

    Складывать начало отрезка с секундой ролика нельзя: ролик собран с
    вырезанными паузами, его время короче исходного и течёт неравномерно.
    """
    shorts = Artifact(paths.shorts / "index.json")
    if not shorts.exists():
        return None
    try:
        files = shorts.read_json().get("files", [])
        entry = next((item for item in files if item.get("index") == index), None)
    except (ValueError, TypeError):
        return None
    if entry is None:
        return None

    timeline = Artifact(paths.analysis / "timeline.json")
    edl = Edl.identity()
    if timeline.exists():
        try:
            edl = Edl.from_dict(timeline.read_json().get("edl", {}))
        except (ValueError, KeyError, TypeError):
            edl = Edl.identity()
    # Границы в готовом ролике записаны в выходном времени, а вырезки
    # считаются во времени исходника — отсюда обратный пересчёт.
    start = float(entry.get("source_start", entry["start"]))
    end = float(entry.get("source_end", entry["end"]))
    return moment_in_clip(edl, start, end, offset)


def _preview_position(paths, metadata: dict[str, Any]) -> float:
    """Момент, по которому судить о кадрировании.

    Первый отобранный кандидат нагляднее середины записи: это реальный кадр
    будущего ролика, а не случайная заставка.
    """
    candidates_artifact = Artifact(paths.analysis / "candidates.json")
    if candidates_artifact.exists():
        try:
            found = candidates_artifact.read_json().get("candidates", [])
            if found:
                start, end = found[0]["start"], found[0]["end"]
                return start + min(2.0, (end - start) / 2)
        except (ValueError, KeyError, TypeError):
            pass
    duration = metadata.get("duration_seconds")
    return float(duration) / 2 if duration else 0.0


# --- обзор кандидатов ------------------------------------------------------


class ReviewPayload(BaseModel):
    """Решение по одному кандидату.

    Все поля необязательны: вердикт и границы правятся независимо друг
    от друга — можно принять клип, не трогая границы, и подвинуть границы,
    не меняя оценки.
    """

    verdict: Literal["accept", "reject"] | None = None
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)


def _candidates_list(paths) -> list[dict[str, Any]]:
    artifact = Artifact(paths.analysis / "candidates.json")
    if not artifact.exists():
        raise HTTPException(status_code=404, detail="кандидаты ещё не отобраны")
    return artifact.read_json().get("candidates", [])


def _review_state(paths) -> dict[str, Any]:
    artifact = Artifact(paths.review)
    if not artifact.exists():
        return review.empty()
    try:
        return artifact.read_json()
    except ValueError:
        # Битый файл разметки не должен закрывать доступ к экрану: решения
        # накапливаются заново, а вот потерять возможность работать хуже.
        log.warning("не читается файл разметки, начинаем заново: %s", paths.review)
        return review.empty()


def _selection_by_index(paths) -> dict[int, dict[str, Any]]:
    """Оценки модели по индексу кандидата.

    Обзор показывает всех кандидатов, а не только отобранных: видеть, что
    модель отбросила, не менее важно — несогласие человека с отбором и есть
    обучающий сигнал (§63).
    """
    artifact = Artifact(paths.analysis / "selection.json")
    if not artifact.exists():
        return {}
    try:
        clips = artifact.read_json().get("clips", [])
    except ValueError:
        return {}
    return {c["index"]: c for c in clips if isinstance(c.get("index"), int)}


def _review_response(paths, video_id: str) -> dict[str, Any]:
    candidates = _candidates_list(paths)
    merged = review.merge(candidates, _review_state(paths))

    scored = _selection_by_index(paths)
    for clip in merged:
        entry = scored.get(clip["index"])
        clip["selected"] = entry is not None
        if entry:
            clip.update(
                interest_score=entry.get("interest_score"),
                rank=entry.get("rank"),
                explanation=entry.get("explanation"),
                factors=entry.get("factors"),
                penalties=entry.get("penalties"),
            )
            # Границы, уточнённые моделью, показываются только если человек
            # их ещё не правил: его решение важнее.
            if not clip.get("edited"):
                clip["start"] = entry["start"]
                clip["end"] = entry["end"]
                clip["duration"] = round(entry["end"] - entry["start"], 2)

    return {
        "video_id": video_id,
        "clips": merged,
        "stats": {**review.stats(merged), "scored": len(scored)},
    }


@app.get("/api/videos/{video_id}/review")
def get_review(video_id: str, project: str = "default") -> dict[str, Any]:
    paths, _ = _paths(video_id, project)
    return _review_response(paths, video_id)


@app.put("/api/videos/{video_id}/review/{index}")
def set_review(
    video_id: str, index: int, payload: ReviewPayload, project: str = "default"
) -> dict[str, Any]:
    paths, _ = _paths(video_id, project)
    candidates = _candidates_list(paths)
    if not 0 <= index < len(candidates):
        raise HTTPException(status_code=404, detail=f"кандидата {index} нет")

    try:
        updated = review.record(
            _review_state(paths),
            index=index,
            candidate=candidates[index],
            verdict=payload.verdict,
            start=payload.start,
            end=payload.end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    Artifact(paths.review).write_json(updated)
    return _review_response(paths, video_id)


@app.delete("/api/videos/{video_id}/review/{index}")
def clear_review(video_id: str, index: int, project: str = "default") -> dict[str, Any]:
    paths, _ = _paths(video_id, project)
    Artifact(paths.review).write_json(review.forget(_review_state(paths), index))
    return _review_response(paths, video_id)


@app.get("/api/videos/{video_id}/publish")
def publish_texts(video_id: str, project: str = "default") -> dict[str, Any]:
    """Заголовки, описания и хэштеги — то, что копируют при публикации."""
    paths, _ = _paths(video_id, project)
    artifact = Artifact(paths.analysis / "publish.json")
    if not artifact.exists():
        raise HTTPException(status_code=404, detail="тексты ещё не сгенерированы")
    data = artifact.read_json()
    for entry in data.get("clips", []):
        # Готовая к вставке строка собирается на сервере: правила склейки
        # описания с хэштегами не должны разъезжаться между CLI и вебом (§33).
        entry["ready"] = publish.render_description(entry)
    return data


#: Высота миниатюры кандидата. Карточка в списке узкая, больше не нужно.
THUMBNAIL_HEIGHT = 240


@app.get("/api/videos/{video_id}/frame")
def frame(
    video_id: str,
    at: float = Query(ge=0),
    project: str = "default",
    height: int = Query(default=THUMBNAIL_HEIGHT, ge=60, le=1080),
):
    """Один кадр исходника в заданный момент.

    Нужен карточкам обзора: рендерить клип ради того, чтобы понять, что
    в кадре, — минуты работы, вытащить кадр — доли секунды.
    """
    paths, _ = _paths(video_id, project)
    try:
        source = find_source(paths.source)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    metadata_artifact = Artifact(paths.metadata)
    metadata = metadata_artifact.read_json() if metadata_artifact.exists() else {}
    if not metadata.get("has_video", True):
        raise HTTPException(status_code=409, detail="в источнике нет картинки")

    target = paths.base / "meta" / f"frame-{height}-{at:.2f}.jpg"
    if not target.exists():
        try:
            run_tool(
                [
                    "ffmpeg", "-nostdin", "-v", "error", "-y",
                    "-ss", f"{at:.3f}",
                    "-i", str(source.resolve()),
                    "-frames:v", "1",
                    "-vf", f"scale=-2:{height}",
                    "-q:v", "4",
                    "-f", "mjpeg",
                    str(target),
                ],
                timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    _trim_frames(paths)
    return serve_file(target, None)


#: Миниатюры служат кэшем, но расти без предела не должны.
FRAME_KEEP = 60


def _trim_frames(paths) -> None:
    files = sorted(
        (paths.base / "meta").glob("frame-*.jpg"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in files[FRAME_KEEP:]:
        stale.unlink(missing_ok=True)


#: Сколько кусков предпросмотра держать на записи. Они служат кэшем, но
#: расти без предела не должны — как миниатюры и кадры рамки.
PREVIEW_CLIPS_KEEP = 40

#: Запас до и после момента. Границы правят по позиции плеера, и без запаса
#: подвинуть начало назад было бы некуда.
PREVIEW_LEAD = 4.0

#: Высота куска предпросмотра. Разметку ведут по содержанию, а не по
#: чёткости: 480 хватает, чтобы понять, что происходит в кадре.
PREVIEW_HEIGHT_SMALL = 480


@app.get("/api/videos/{video_id}/review/{index}/media")
def review_media(
    video_id: str,
    index: int,
    project: str = "default",
    range_header: Annotated[str | None, Header(alias="range")] = None,
):
    """Один момент отдельным файлом — для обзора вместо целой записи.

    **Зачем.** Обзор играл исходник с перемотками, и замер показал, во что
    это обходится: пятнадцать секунд просмотра на записи в 4.58 часа
    вытянули 26.7 ГБ — вчетверо больше самого файла. Браузер на каждой
    перемотке запрашивает новый кусок и бросает предыдущий, а раздаёт их
    сервер по-настоящему. На домашнем канале один сеанс разметки съел бы
    весь трафик.

    **Почему кусок перекодируется, а не копируется.** Копирование потока
    быстрее, но начинается с ближайшего опорного кадра — то есть раньше
    запрошенного и на неизвестную величину. Обзор правит границы по позиции
    плеера, и смещение, которого никто не знает, сдвинуло бы их все.
    Перекодирование двадцати секунд в 480p занимает секунду-другую, зато
    начало точное, а вес падает ещё вчетверо.

    **Почему не лёгкий двойник всей записи.** Он дал бы меньше трафика, но
    стоил бы часа работы процессора на каждую запись — включая те моменты,
    которые никто не откроет. Здесь платят только за просмотренное.
    """
    paths, _ = _paths(video_id, project)
    clips = _candidates_list(paths)
    if not 0 <= index < len(clips):
        raise HTTPException(status_code=404, detail=f"момента {index} нет")

    merged = review.merge(clips, _review_state(paths))[index]
    start, end = float(merged["start"]), float(merged["end"])

    try:
        source = find_source(paths.source)
    except MediaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    begin = max(start - PREVIEW_LEAD, 0.0)
    target = paths.base / "meta" / f"moment-{index:03d}-{start:.1f}-{end:.1f}.mp4"
    if not target.exists():
        try:
            run_tool(
                [
                    "ffmpeg", "-nostdin", "-v", "error", "-y",
                    # Перемотка до входа: так ffmpeg не читает файл с начала.
                    "-ss", f"{begin:.3f}",
                    "-i", str(source.resolve()),
                    "-t", f"{end - begin + PREVIEW_LEAD:.3f}",
                    "-vf", f"scale=-2:{PREVIEW_HEIGHT_SMALL}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "64k", "-ac", "1",
                    "-movflags", "+faststart",
                    str(target),
                ],
                timeout=300,
            )
        except Exception as exc:  # noqa: BLE001
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    _trim_previews_of(paths, "moment-*.mp4", PREVIEW_CLIPS_KEEP)
    response = serve_file(target, range_header)
    # Смещение куска относительно записи. Интерфейс пересчитывает по нему
    # позицию плеера во время записи — иначе правка границ уехала бы.
    response.headers["X-Fragment-Start"] = f"{begin:.3f}"
    return response


def _trim_previews_of(paths, pattern: str, keep: int) -> None:
    files = sorted(
        (paths.base / "meta").glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for stale in files[keep:]:
        stale.unlink(missing_ok=True)


@app.get("/api/videos/{video_id}/media")
def media(
    video_id: str,
    project: str = "default",
    range_header: Annotated[str | None, Header(alias="range")] = None,
):
    paths, _ = _paths(video_id, project)
    try:
        source = find_source(paths.source)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return serve_file(source, range_header)


#: Насколько держим каталог моделей. У поставщика он меняется раз в дни, а
#: экран настроек спрашивает его при каждом открытии записи — и раз в три
#: секунды, пока работа ждёт очереди. Каждый такой запрос уходил на сторону.
MODELS_TTL_SECONDS = 300.0

#: Поставщик → когда получен и что получено. Гонка двух запросов приводит
#: к лишнему походу за каталогом и только — записывается тот же список.
_MODEL_CATALOGUE: dict[str, tuple[float, list[Any]]] = {}


def _catalogue(provider: str, key: str | None) -> list[Any]:
    cached = _MODEL_CATALOGUE.get(provider)
    if cached is not None and time.monotonic() - cached[0] < MODELS_TTL_SECONDS:
        return cached[1]
    fetched = llm.fetch_models(key, provider_name=provider)
    _MODEL_CATALOGUE[provider] = (time.monotonic(), fetched)
    return fetched


@app.get("/api/settings/models")
def available_models(free_only: bool = True) -> dict[str, Any]:
    """Модели, из которых можно выбирать, и та, что выбрана сейчас."""
    config, _ = _config()
    try:
        current = llm.provider(config.llm.provider)
    except llm.LlmError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    key = llm.api_key(provider_name=config.llm.provider)
    try:
        catalogue = _catalogue(config.llm.provider, key)
    except llm.LlmError as exc:
        # Каталог недоступен — это не повод ронять экран настроек: выбранная
        # модель известна и без него.
        log.warning("каталог моделей недоступен: %s", exc)
        return {
            "provider": current.name,
            "selected": config.llm.model,
            "models": [],
            "error": str(exc),
        }

    shown = llm.rank_free(catalogue) if free_only else [
        m for m in catalogue if m.is_text and m.structured
    ]
    return {
        "provider": current.name,
        "selected": config.llm.model,
        "has_key": key is not None,
        "models": [
            {
                "id": m.id,
                "context": m.context_length,
                "free": m.is_free,
                "structured": m.structured,
            }
            for m in shown[:40]
        ],
    }


@app.get("/api/settings/detectors")
def available_detectors() -> dict[str, Any]:
    """Бэкенды компьютерного зрения с честной пометкой о готовности."""
    config, _ = _config()
    return {"selected": config.detector.backend, "backends": detectors.describe_backends()}


# --- контур сбора данных (§63) ---------------------------------------------


class PublishRequest(BaseModel):
    index: int
    platform: str = "youtube"
    url: str | None = None


class MetricsRequest(BaseModel):
    clip_id: str
    platform: str | None = None
    measured_at: str | None = None
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    retention: float | None = Field(default=None, ge=0, le=1)
    ctr: float | None = Field(default=None, ge=0, le=1)
    note: str | None = None


def _performance_payload(config, video_id: str) -> dict[str, Any]:
    with db.connect(config.storage_root) as connection:
        rows = db.clip_rows(connection, video_id)
        for row in rows:
            row["history"] = db.measurements(connection, row["clip_id"])
    return {"clips": rows, "report": feedback.report(rows)}


@app.get("/api/videos/{video_id}/performance")
def performance(video_id: str, project: str = "default") -> dict[str, Any]:
    """Опубликованные клипы, их метрики и отчёт по связи признаков с результатом."""
    _paths(video_id, project)
    config, _ = _config()
    return _performance_payload(config, video_id)


@app.post("/api/videos/{video_id}/performance/publish")
def mark_published(video_id: str, payload: PublishRequest, project: str = "default") -> dict[str, Any]:
    """Фиксирует клип: с этого момента его вектор признаков неизменяем (§63)."""
    paths, config = _paths(video_id, project)

    selection = Artifact(paths.analysis / "selection.json")
    if not selection.exists():
        raise HTTPException(status_code=409, detail="отбор моделью не выполнялся")
    data = selection.read_json()
    chosen = next((c for c in data.get("clips", []) if c.get("index") == payload.index), None)
    if chosen is None:
        raise HTTPException(status_code=404, detail=f"клипа {payload.index} нет в отборе")

    verdict, shift = None, (None, None)
    review_artifact = Artifact(paths.review)
    if review_artifact.exists():
        try:
            entry = review.by_index(review_artifact.read_json()).get(payload.index)
        except ValueError:
            entry = None
        if entry:
            verdict = entry.get("verdict")
            shift = (
                round(entry["start"] - chosen["start"], 3),
                round(entry["end"] - chosen["end"], 3),
            )

    meta = Artifact(paths.metadata)
    metadata = meta.read_json() if meta.exists() else {}

    title = None
    publish_artifact = Artifact(paths.analysis / "publish.json")
    if publish_artifact.exists():
        try:
            title = next(
                (e.get("title") for e in publish_artifact.read_json().get("clips", [])
                 if e.get("index") == payload.index),
                None,
            )
        except ValueError:
            title = None

    with db.connect(config.storage_root) as connection:
        db.upsert_video(
            connection,
            video_id=video_id,
            project_id=project,
            title=registry.display_title(metadata, video_id),
            content_origin=metadata.get("content_origin", "own"),
            retention_until=metadata.get("retention_until"),
        )
        db.freeze_clip(
            connection,
            clip={**chosen, "prompt_version": data.get("prompt_version")},
            video_id=video_id,
            weights=data.get("weights"),
            title=title,
            platform=payload.platform,
            url=payload.url,
            published_at=db.now(),
            human_verdict=verdict,
            bounds_shift=shift,
        )

    log.info("видео %s: клип %d опубликован на %s", video_id, payload.index, payload.platform)
    return _performance_payload(config, video_id)


@app.post("/api/videos/{video_id}/performance/metrics")
def add_metrics(video_id: str, payload: MetricsRequest, project: str = "default") -> dict[str, Any]:
    _paths(video_id, project)
    config, _ = _config()
    with db.connect(config.storage_root) as connection:
        row = connection.execute(
            "SELECT platform FROM clips WHERE clip_id = ?", (payload.clip_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="клип не зафиксирован — сначала отметьте публикацию")
        try:
            db.add_measurement(
                connection,
                clip_id=payload.clip_id,
                platform=payload.platform or row["platform"] or "other",
                measured_at=payload.measured_at,
                views=payload.views, likes=payload.likes, comments=payload.comments,
                shares=payload.shares, retention=payload.retention, ctr=payload.ctr,
                note=payload.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _performance_payload(config, video_id)


# --- собранный интерфейс ---------------------------------------------------

#: Каталог сборки фронтенда. Ищется относительно кода, а не текущей папки:
#: сервер должен подниматься из любого места (§58).
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def mount_frontend() -> bool:
    """Отдаёт собранный интерфейс с того же адреса, что и API.

    Монтируется последним: маршруты /api зарегистрированы выше и обрабатываются
    раньше, поэтому статика их не перехватывает. Хэш-роутинг во фронтенде
    означает, что для всех экранов достаточно одного index.html.
    """
    if not (FRONTEND_DIST / "index.html").is_file():
        return False
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return True
