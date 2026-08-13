"""HTTP API.

BAZA.md §33: интерфейс работает поверх того же слоя, что и CLI — ни одной
операции только в одном месте. Каждый эндпоинт здесь вызывает те же функции
ядра, что и команда в консоли.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from narezka.api import jobs
from narezka.api.media import serve_file
from narezka.core import env
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
from narezka.core.media import find_source, run_tool
from narezka.core.paths import list_videos, video_paths
from narezka.core.runner import run_pipeline, run_stage
from narezka.core.stage import StageContext
from narezka.stages import PIPELINE, get_stage
from narezka.stages.render import load_framing, source_size

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


def _paths(video_id: str, project: str):
    config, _ = _config()
    paths = video_paths(config.storage_root, project, video_id)
    if not paths.exists():
        raise HTTPException(status_code=404, detail=f"видео {video_id} не найдено")
    return paths, config


def _context(video_id: str, project: str, profile: str | None = None) -> StageContext:
    config, device = _config(profile)
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


@app.get("/api/stages")
def stages() -> list[dict[str, Any]]:
    return [
        {"name": s.name, "description": s.description, "device": s.device.value, "optional": s.optional}
        for s in PIPELINE
    ]


# --- видео -----------------------------------------------------------------


class AddVideo(BaseModel):
    url: str | None = None
    file: str | None = None
    project: str = "default"


@app.get("/api/videos")
def videos(project: str = "default") -> list[dict[str, Any]]:
    config, _ = _config()
    result = []
    for video_id in list_videos(config.storage_root, project):
        paths = video_paths(config.storage_root, project, video_id)
        meta = Artifact(paths.metadata)
        data = meta.read_json() if meta.exists() else {}
        job = jobs.manager.get(video_id)
        result.append(
            {
                "video_id": video_id,
                "title": data.get("source_title") or data.get("source_file") or video_id,
                "origin": data.get("origin", {}).get("type"),
                "duration_seconds": data.get("duration_seconds"),
                "video": data.get("video"),
                "job_status": job.status if job else None,
            }
        )
    return result


@app.post("/api/videos")
def add_video(payload: AddVideo) -> dict[str, Any]:
    if bool(payload.url) == bool(payload.file):
        raise HTTPException(status_code=400, detail="укажите ровно одно: url или file")

    config, _ = _config()

    if payload.url:
        url = payload.url.strip()
        video_id = hashlib.sha256(url.encode()).hexdigest()[:12]
        origin = {"type": "url", "url": url}
    else:
        assert payload.file
        source = Path(payload.file).expanduser()
        if not source.is_file():
            raise HTTPException(status_code=400, detail=f"файл не найден: {source}")
        video_id = hashlib.sha256(f"{source.stat().st_size}:{source.name}".encode()).hexdigest()[:12]
        origin = {"type": "local_file", "path": str(source.resolve())}

    paths = video_paths(config.storage_root, payload.project, video_id)
    paths.ensure()

    if payload.file:
        target = paths.source / Path(payload.file).name
        if not target.exists():
            try:
                target.symlink_to(Path(payload.file).expanduser().resolve())
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"не удалось привязать файл: {exc}") from exc

    metadata = Artifact(paths.metadata)
    existing = metadata.read_json() if metadata.exists() else {}
    existing.update(
        {
            "video_id": video_id,
            "project_id": payload.project,
            "origin": origin,
            "content_origin": "own",
            "retention_until": None,
        }
    )
    metadata.write_json(existing)
    return {"video_id": video_id}


@app.get("/api/videos/{video_id}")
def video_detail(video_id: str, project: str = "default") -> dict[str, Any]:
    paths, _ = _paths(video_id, project)
    meta = Artifact(paths.metadata)
    cost = Artifact(paths.cost)
    job = jobs.manager.get(video_id)
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
    job = jobs.manager.get(video_id)
    if job and job.is_active:
        raise HTTPException(status_code=409, detail="видео обрабатывается — дождитесь завершения")
    shutil.rmtree(paths.base)
    return {"status": "deleted"}


# --- запуск обработки ------------------------------------------------------


class RunRequest(BaseModel):
    stage: str | None = None
    force: bool = False
    profile: str | None = None
    project: str = "default"


@app.post("/api/videos/{video_id}/run")
def run(video_id: str, payload: RunRequest) -> dict[str, Any]:
    ctx = _context(video_id, payload.project, payload.profile)
    stage = get_stage(payload.stage) if payload.stage else None

    def work(emit) -> None:
        observer = lambda name, event, data: emit(name, event, data)  # noqa: E731
        if stage is not None:
            run_stage(stage, ctx, force=payload.force, observer=observer)
        else:
            run_pipeline(list(PIPELINE), ctx, force=payload.force, observer=observer)

    job, created = jobs.manager.start(video_id, payload.project, work)
    return {"started": created, "status": job.status}


@app.get("/api/videos/{video_id}/events")
async def events(video_id: str, since: int = Query(0)) -> StreamingResponse:
    """Поток событий обработки (§69: прогресс в реальном времени)."""

    async def stream():
        cursor = since
        idle_ticks = 0
        while True:
            job = jobs.manager.get(video_id)
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


#: Высота картинки предпросмотра. Хватает, чтобы оценить рамку и читаемость,
#: но кодируется мгновенно.
PREVIEW_HEIGHT = 640


class FramingPayload(FramingConfig):
    """Настройки кадрирования, присланные из интерфейса.

    Наследует проверки диапазонов у конфига — отдельную схему держать незачем.
    """


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

    return {
        "current": current.__dict__,
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
