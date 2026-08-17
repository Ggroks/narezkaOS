"""HTTP API.

BAZA.md §33: интерфейс работает поверх того же слоя, что и CLI — ни одной
операции только в одном месте. Каждый эндпоинт здесь вызывает те же функции
ядра, что и команда в консоли.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from narezka.api import auth, jobs
from narezka.api.media import serve_file
from narezka.core import env
from narezka.core.clips import load_clips
from narezka.core.artifacts import Artifact
from narezka.core.config import FramingConfig, load_config
from narezka.core.device import detect_device
from narezka.core.logging import get_logger
from narezka.core.framing import (
    Framing,
    build_filter,
    describe,
    plan_frame,
    preview_presets,
)
from narezka.core.media import MediaError, find_source, run_tool
from narezka.core import accounts, db, detectors, feedback, llm, publish, registry, review, settings
from narezka.core.paths import list_videos, video_paths
from narezka.core.runner import run_pipeline, run_stage
from narezka.core.stage import StageContext
from narezka.stages import GROUPS, PIPELINE, get_stage, stages_for
from narezka.stages.render import load_framing, load_options, source_size

app = FastAPI(title="Narezka OS", version="0.1.0")
log = get_logger("api")

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


@app.get("/api/auth/me")
def whoami(request: Request) -> dict[str, Any]:
    """Кто вошёл и нужен ли вход вообще.

    Отвечает всегда, а не 401: интерфейс по этому ответу решает, показать
    ему форму входа или рабочий экран.
    """
    config, _ = _config()
    return auth.describe(auth.current_user(request, config), config)


@app.post("/api/auth/login")
def login(payload: Credentials) -> JSONResponse:
    config, _ = _config()
    if not config.auth.enabled:
        return JSONResponse(auth.describe(accounts.LOCAL_USER, config))

    with db.connect(config.storage_root) as connection:
        user = accounts.verify(connection, login=payload.login, password=payload.password)
        if user is None:
            # Один ответ на «нет такого логина» и «неверный пароль»: разница
            # между ними — готовый список зарегистрированных.
            raise HTTPException(status_code=401, detail="неверный логин или пароль")
        token = accounts.open_session(connection, user)

    log.info("вход: %s", user.login)
    response = JSONResponse(auth.describe(user, config))
    _set_session(response, token, config)
    return response


@app.post("/api/auth/signup")
def signup(payload: Credentials) -> JSONResponse:
    config, _ = _config()
    if not config.auth.enabled or not config.auth.allow_signup:
        raise HTTPException(status_code=403, detail="регистрация закрыта")

    with db.connect(config.storage_root) as connection:
        try:
            user = accounts.create(connection, login=payload.login, password=payload.password)
        except accounts.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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


class RenameVideo(BaseModel):
    #: Пустая строка означает возврат к заголовку из источника.
    title: str = Field(default="", max_length=registry.TITLE_LIMIT)


def _summary(config, project: str, video_id: str) -> dict[str, Any]:
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

    job = jobs.manager.get(video_id, project)
    if job is not None and job.is_active:
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
    return [_summary(config, space, vid) for vid in list_videos(config.storage_root, space)]


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


@app.get("/api/videos/{video_id}")
def video_detail(video_id: str, project: str = "default") -> dict[str, Any]:
    paths, _ = _paths(video_id, project)
    meta = Artifact(paths.metadata)
    cost = Artifact(paths.cost)
    job = jobs.manager.get(video_id, _workspace(project))
    return {
        "video_id": video_id,
        "project": project,
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
    ctx = _context(video_id, payload.project, payload.profile)
    stage = get_stage(payload.stage) if payload.stage else None

    if payload.group is not None:
        try:
            planned = stages_for(payload.group)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        planned = list(PIPELINE)

    def work(emit) -> None:
        observer = lambda name, event, data: emit(name, event, data)  # noqa: E731
        if stage is not None:
            run_stage(stage, ctx, force=payload.force, observer=observer)
        else:
            run_pipeline(
                planned, ctx, force=payload.force, observer=observer,
                should_stop=lambda: jobs.manager.get(video_id, ctx.project_id).stop_requested,
            )

    job, created = jobs.manager.start(video_id, ctx.project_id, work)
    return {"started": created, "status": job.status}


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


@app.get("/api/settings/encoders")
def encoders_info() -> dict[str, Any]:
    """Чем сжимать видео — с плюсами и минусами каждого варианта."""
    from narezka.core import encoders as enc  # noqa: PLC0415

    return {"selected": load_config().output.encoder, "encoders": enc.describe_encoders()}


@app.post("/api/videos/{video_id}/stop")
def stop(video_id: str, project: str = "default") -> dict[str, Any]:
    """Останавливает обработку после текущей стадии, не трогая сервер."""
    if not jobs.manager.stop(video_id, _workspace(project)):
        raise HTTPException(status_code=409, detail="обработка не идёт — останавливать нечего")
    return {"stopping": True}


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


#: Высота картинки предпросмотра. С запасом под экраны с двойной плотностью:
#: карточка в интерфейсе около 400 px, и кадр ровно в её размер выглядит мылом.
PREVIEW_HEIGHT = 960


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

    options = load_options(ctx)
    stored = {}
    if Artifact(paths.framing).exists():
        try:
            stored = Artifact(paths.framing).read_json()
        except ValueError:
            stored = {}
    options["llm_model"] = stored.get("llm_model") or ctx.config.llm.model
    options["detector_backend"] = stored.get("detector_backend") or ctx.config.detector.backend
    options["encoder"] = stored.get("encoder") or ctx.config.output.encoder
    for flag in ("use_loudness", "use_speech_rate", "use_chat", "use_chat_reactions"):
        value = stored.get(flag)
        options[flag] = getattr(ctx.config.candidates, flag) if value is None else value
    for tag in ("laughter", "music", "shout", "applause", "crowd"):
        value = stored.get(f"tag_{tag}")
        options[f"tag_{tag}"] = getattr(ctx.config.audiotags, tag) if value is None else value
    for knob in ("track_smoothing", "track_dead_zone"):
        value = stored.get(knob)
        options[knob] = getattr(ctx.config.output.framing, knob) if value is None else value
    options["chat_ignore_start"] = (
        stored.get("chat_ignore_start")
        if isinstance(stored.get("chat_ignore_start"), bool)
        else ctx.config.candidates.chat_ignore_start
    )
    return {
        "current": {**current.__dict__, **options},
        "split_available": split_available,
        "custom": Artifact(paths.framing).exists(),
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
    paths, _ = _paths(video_id, project)
    Artifact(paths.framing).write_json(payload.model_dump())
    # Кадрирование входит в ключ кэша стадии render, поэтому отдельно ничего
    # сбрасывать не нужно: следующий запуск увидит другой ключ и перерендерит.
    log.info("видео %s: кадрирование «%s»", video_id, payload.preset)
    return framing(video_id, project)


@app.delete("/api/videos/{video_id}/framing")
def reset_framing(video_id: str, project: str = "default") -> dict[str, Any]:
    paths, _ = _paths(video_id, project)
    paths.framing.unlink(missing_ok=True)
    return framing(video_id, project)


@app.get("/api/videos/{video_id}/framing/preview")
def framing_preview(
    video_id: str,
    project: str = "default",
    at: float | None = Query(default=None, ge=0),
    preset: str | None = None,
    side_crop: float | None = Query(default=None, ge=0, le=0.95),
    anchor: str | None = None,
    background: str | None = None,
    blur_sigma: float | None = Query(default=None, ge=0, le=200),
    color: str | None = None,
):
    """Один кадр в готовой рамке — чтобы настраивать глазами, а не наугад.

    Параметры перекрывают сохранённые: интерфейс показывает результат ещё до
    того, как пользователь нажал «Сохранить». Полный рендер ради проверки
    геометрии занимает минуты, один кадр — доли секунды.
    """
    paths, ctx, current, src_w, src_h = _framing_state(video_id, project)

    overrides = {
        key: value
        for key, value in {
            "preset": preset,
            "side_crop": side_crop,
            "anchor": anchor,
            "background": background,
            "blur_sigma": blur_sigma,
            "color": color,
        }.items()
        if value is not None
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

    position = at if at is not None else _preview_position(paths, metadata)
    short = ctx.config.output.short
    plan = plan_frame(src_w, src_h, short.width, short.height, requested)

    # Имя от параметров: несколько вкладок с разными настройками не затрут
    # предпросмотр друг друга.
    slug = hashlib.sha256(
        json.dumps({**requested.__dict__, "at": round(position, 2)}, sort_keys=True).encode()
    ).hexdigest()[:12]
    target = paths.base / "meta" / f"preview-{slug}.jpg"

    # Предпросмотр смотрят в браузере на небольшой карточке — полный размер
    # 1080x1920 тут не нужен. Уменьшение встраивается в саму цепочку: -vf
    # нельзя применить к потоку, который уже пришёл из -filter_complex.
    chain = build_filter(plan, requested, short.width, short.height)
    chain = chain.removesuffix("[v]") + f",scale=-2:{PREVIEW_HEIGHT}[v]"

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
    return serve_file(target, None)


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
        catalogue = llm.fetch_models(key, provider_name=config.llm.provider)
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
