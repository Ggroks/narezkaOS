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
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
from narezka.core import db, feedback, publish, review
from narezka.core.paths import list_videos, video_paths
from narezka.core.runner import run_pipeline, run_stage
from narezka.core.stage import StageContext
from narezka.stages import PIPELINE, get_stage
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
                clip_type=entry.get("clip_type"),
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
            title=metadata.get("source_title") or metadata.get("source_file"),
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
