"""Командный интерфейс.

BAZA.md §33: каждая операция доступна и из CLI, и (позже) из UI — они работают
поверх одного и того же слоя, без приватных путей.
§58: любая стадия запускается изолированно по video-id над артефактами хранилища.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from narezka.core import env
from narezka.core.artifacts import Artifact, cleanup_partials
from narezka.core.config import load_config
from narezka.core.logging import get_logger, setup_logging
from narezka.core.media import MEDIA_SUFFIXES
from narezka.core.paths import list_videos, video_paths
from narezka.core.runner import Outcome, run_pipeline, run_stage
from narezka.core.stage import StageContext
from narezka.stages import PIPELINE, REGISTRY, get_stage

app = typer.Typer(add_completion=False, help="Narezka OS — AI-монтажёр длинных видео")
console = Console()

OUTCOME_STYLE = {
    Outcome.DONE: "green",
    Outcome.CACHED: "cyan",
    Outcome.SKIPPED: "yellow",
    Outcome.FAILED: "red",
}


def _build_context(video_id: str, project: str, profile: str | None, config_path: Path | None) -> StageContext:
    probe_config = load_config(config_path)
    device = env.ensure_ready(probe_config.storage_root, probe_config.device)
    config = load_config(config_path, profile_override=profile, has_accelerator=device.is_accelerator)

    paths = video_paths(config.storage_root, project, video_id)
    if not paths.exists():
        console.print(f"[red]Видео '{video_id}' не найдено в проекте '{project}'.[/red]")
        console.print("Добавьте файл: [bold]narezka add --file путь/к/видео.mp4[/bold]")
        raise typer.Exit(1)

    return StageContext(
        project_id=project,
        video_id=video_id,
        paths=paths,
        config=config,
        device=device,
        log=get_logger("stage"),
    )


def _video_id_for_file(path: Path) -> str:
    """Идентификатор по содержимому: повторное добавление того же файла даёт тот же id."""
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(1024 * 1024))
        if size > 2 * 1024 * 1024:
            handle.seek(-1024 * 1024, os.SEEK_END)
            digest.update(handle.read(1024 * 1024))
    return digest.hexdigest()[:12]


def _place_source(src: Path, dst: Path) -> str:
    """Жёсткая ссылка, иначе символическая, иначе копия.

    Копировать десятки гигабайт ради того, чтобы файл лежал «внутри» хранилища,
    смысла нет (§65).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return "уже на месте"
    try:
        os.link(src, dst)
        return "жёсткая ссылка"
    except OSError:
        pass
    try:
        dst.symlink_to(src.resolve())
        return "символическая ссылка"
    except OSError:
        pass
    shutil.copy2(src, dst)
    return "копия"


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Подробный лог")] = False) -> None:
    setup_logging("DEBUG" if verbose else "INFO")
    # Ключи читаются из .env до всего остального: без них часть команд
    # просто не сможет ничего сделать, и узнать об этом лучше сразу.
    load_dotenv(Path(".env"), override=False)
    for note in env.normalize_proxy_env():
        get_logger().debug("прокси — %s", note)


@app.command()
def doctor(
    config_path: Annotated[Path | None, typer.Option("--config", help="Путь к config.yaml")] = None,
) -> None:
    """Проверить готовность окружения."""
    config = load_config(config_path)
    checks, _ = env.run_checks(config.storage_root, config.device)

    table = Table(title="Окружение", show_lines=False)
    table.add_column("")
    table.add_column("Проверка", style="bold")
    table.add_column("Значение")

    for check in checks:
        if check.ok:
            mark, style = "[green]OK[/green]", ""
        elif check.critical:
            mark, style = "[red]НЕТ[/red]", "red"
        else:
            mark, style = "[yellow]![/yellow]", "yellow"
        table.add_row(mark, check.name, f"[{style}]{check.detail}[/{style}]" if style else check.detail)

    console.print(table)

    blocking = [c for c in checks if c.critical and not c.ok]
    if blocking:
        console.print("\n[red]Есть критичные проблемы — обработка не запустится.[/red]")
        raise typer.Exit(1)
    console.print("\n[green]Окружение готово.[/green]")


@app.command()
def add(
    file: Annotated[Path | None, typer.Option("--file", "-f", help="Локальный медиафайл", exists=True)] = None,
    url: Annotated[str | None, typer.Option("--url", "-u", help="YouTube или Twitch VOD")] = None,
    project: Annotated[str, typer.Option("--project", "-p")] = "default",
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Зарегистрировать видео проекта: локальный файл или URL."""
    if (file is None) == (url is None):
        console.print("[red]Укажите ровно одно: --file или --url.[/red]")
        raise typer.Exit(1)

    config = load_config(config_path)

    if file is not None:
        if file.suffix.lower() not in MEDIA_SUFFIXES:
            console.print(f"[yellow]Расширение {file.suffix} не в списке известных — пробую всё равно.[/yellow]")
        video_id = _video_id_for_file(file)
        origin = {"type": "local_file", "path": str(file.resolve())}
    else:
        assert url is not None
        video_id = hashlib.sha256(url.strip().encode()).hexdigest()[:12]
        origin = {"type": "url", "url": url.strip()}

    paths = video_paths(config.storage_root, project, video_id)
    paths.ensure()

    how = _place_source(file, paths.source / file.name) if file is not None else "будет скачано"

    metadata = Artifact(paths.metadata)
    existing = metadata.read_json() if metadata.exists() else {}
    existing.update(
        {
            "video_id": video_id,
            "project_id": project,
            "origin": origin,
            # §31: поля закладываются сразу, чтобы потом не мигрировать схему.
            "content_origin": "own",
            "retention_until": None,
        }
    )
    metadata.write_json(existing)

    console.print(f"[green]Добавлено[/green] video_id=[bold]{video_id}[/bold] ({how})")
    console.print(f"Дальше: [bold]narezka pipeline --video-id {video_id}[/bold]")


@app.command()
def stages() -> None:
    """Показать доступные стадии."""
    table = Table(title="Стадии пайплайна")
    table.add_column("#")
    table.add_column("Стадия", style="bold")
    table.add_column("Устройство")
    table.add_column("Описание")
    for index, stage in enumerate(PIPELINE, 1):
        table.add_row(str(index), stage.name, stage.device.value, stage.description)
    console.print(table)
    if not PIPELINE:
        console.print("[yellow]Стадий пока нет.[/yellow]")


@app.command()
def run(
    stage_name: Annotated[str, typer.Argument(help="Имя стадии")],
    video_id: Annotated[str, typer.Option("--video-id", "-i")],
    project: Annotated[str, typer.Option("--project", "-p")] = "default",
    force: Annotated[bool, typer.Option("--force", help="Игнорировать кэш")] = False,
    profile: Annotated[str | None, typer.Option("--profile", help="dev | batch")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Запустить одну стадию."""
    try:
        stage = get_stage(stage_name)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    ctx = _build_context(video_id, project, profile, config_path)
    result = run_stage(stage, ctx, force=force)
    style = OUTCOME_STYLE[result.outcome]
    console.print(f"[{style}]{result.stage}: {result.outcome.value}[/{style}]" + (f" — {result.reason}" if result.reason else ""))
    if not result.ok:
        raise typer.Exit(1)


@app.command()
def pipeline(
    video_id: Annotated[str, typer.Option("--video-id", "-i")],
    project: Annotated[str, typer.Option("--project", "-p")] = "default",
    force: Annotated[bool, typer.Option("--force", help="Игнорировать кэш")] = False,
    profile: Annotated[str | None, typer.Option("--profile", help="dev | batch")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Прогнать весь пайплайн."""
    ctx = _build_context(video_id, project, profile, config_path)
    console.print(f"Профиль [bold]{ctx.config.resolved_profile}[/bold], устройство [bold]{ctx.device.kind}[/bold]")

    results = run_pipeline(list(PIPELINE), ctx, force=force)

    table = Table(title="Итог")
    table.add_column("Стадия", style="bold")
    table.add_column("Результат")
    table.add_column("Время")
    for result in results:
        style = OUTCOME_STYLE[result.outcome]
        table.add_row(
            result.stage,
            f"[{style}]{result.outcome.value}[/{style}]" + (f" — {result.reason}" if result.reason else ""),
            f"{result.duration:.1f} с" if result.duration else "",
        )
    console.print(table)

    if any(not r.ok for r in results):
        raise typer.Exit(1)


@app.command()
def framing(
    video_id: Annotated[str, typer.Option("--video-id", "-i")],
    project: Annotated[str, typer.Option("--project", "-p")] = "default",
    preset: Annotated[
        str | None,
        typer.Option("--preset", help="full | balanced | focus | fill | custom"),
    ] = None,
    side_crop: Annotated[
        float | None,
        typer.Option("--side-crop", help="Доля обрезки по бокам при --preset custom, 0..0.95"),
    ] = None,
    anchor: Annotated[
        str | None, typer.Option("--anchor", help="center | left | right")
    ] = None,
    background: Annotated[
        str | None, typer.Option("--background", help="blur | color")
    ] = None,
    blur_sigma: Annotated[
        float | None, typer.Option("--blur", help="Сила размытия, 0 отключает")
    ] = None,
    color: Annotated[str | None, typer.Option("--color", help="Цвет полос, например 0x14171c")] = None,
    reset: Annotated[bool, typer.Option("--reset", help="Вернуть настройки из конфига")] = False,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Кадрирование вертикального ролика: обрезка по бокам, полосы, размытие.

    Без параметров показывает текущие настройки и что дадут готовые варианты
    на этом исходнике.
    """
    from narezka.core.config import FramingConfig  # noqa: PLC0415
    from narezka.core.framing import (  # noqa: PLC0415
        PRESET_LABELS,
        Framing,
        describe,
        plan_frame,
        preview_presets,
    )
    from narezka.stages.render import load_framing, source_size  # noqa: PLC0415

    ctx = _build_context(video_id, project, None, config_path)

    if reset:
        ctx.paths.framing.unlink(missing_ok=True)
        console.print("[green]Настройки кадрирования сброшены к конфигу.[/green]")

    changes = {
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
    if changes:
        try:
            updated = FramingConfig(**{**load_framing(ctx).__dict__, **changes})
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None
        Artifact(ctx.paths.framing).write_json(updated.model_dump())
        console.print("[green]Сохранено. Ролики перерендерятся при следующем запуске render.[/green]")

    current = load_framing(ctx)
    metadata = Artifact(ctx.paths.metadata)
    src_w, src_h = source_size(metadata.read_json() if metadata.exists() else {})
    short = ctx.config.output.short

    table = Table(title=f"Кадрирование {src_w}x{src_h} → {short.width}x{short.height}")
    table.add_column("")
    table.add_column("Вариант", style="bold")
    table.add_column("Видно", justify="right")
    table.add_column("Срез по бокам", justify="right")
    for entry in preview_presets(src_w, src_h, short.width, short.height):
        chosen = entry["preset"] == current.preset
        table.add_row(
            "→" if chosen else "",
            entry["label"],
            f"{entry['content_share'] * 100:.0f}%",
            f"{entry['side_crop'] * 100:.0f}%",
        )
    if current.preset == "custom":
        plan = plan_frame(src_w, src_h, short.width, short.height, current)
        table.add_row(
            "→",
            PRESET_LABELS["custom"],
            f"{plan.content_share * 100:.0f}%",
            f"{plan.lost_share * 100:.0f}%",
        )
    console.print(table)

    plan = plan_frame(src_w, src_h, short.width, short.height, current)
    backdrop = (
        "кадр заполнен целиком"
        if plan.full_bleed
        else f"однотонная {current.color}"
        if current.background == "color"
        else "кадр без размытия"
        if current.blur_sigma == 0
        else f"размытый кадр, сила {current.blur_sigma:g}"
    )
    console.print(
        f"Сейчас: [bold]{PRESET_LABELS.get(current.preset, current.preset)}[/bold]"
        f", {describe(plan)}. Полосы: {backdrop}."
    )
    if not Artifact(ctx.paths.framing).exists():
        console.print("[dim]Настройки берутся из конфига — вручную для этого видео не заданы.[/dim]")


@app.command()
def models(
    check: Annotated[bool, typer.Option("--check", help="Пробный запрос выбранной моделью")] = False,
    all_models: Annotated[bool, typer.Option("--all", help="Показать и платные тоже")] = False,
    limit: Annotated[int, typer.Option("--limit", help="Сколько строк показать")] = 15,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Модели у провайдера: какие доступны и работает ли ключ.

    Список бесплатных моделей меняется — они появляются, исчезают
    и переименовываются. Поэтому имя модели живёт в конфиге, а актуальные
    варианты смотрят этой командой, а не по памяти.
    """
    from narezka.core import llm  # noqa: PLC0415

    config = load_config(config_path)
    key = llm.api_key()

    if check:
        if not key:
            console.print(
                f"[red]Нет ключа.[/red] Положите его в .env как "
                f"[bold]{llm.API_KEY_ENV}[/bold] — образец в .env.example."
            )
            raise typer.Exit(1)
        if not config.llm.model:
            console.print("[red]В конфиге не выбрана модель[/red] (llm.model). "
                          "Посмотрите варианты: narezka models")
            raise typer.Exit(1)

        chain = [config.llm.model, *config.llm.fallback_models]

        def report(model: str, attempt: int) -> None:
            suffix = f" (попытка {attempt + 1})" if attempt else ""
            console.print(f"[dim]пробую {model}{suffix}…[/dim]")

        try:
            result = llm.check_key(
                key, chain, timeout=config.llm.timeout_seconds, on_attempt=report
            )
        except llm.LlmError as exc:
            console.print(f"[red]{exc}[/red]")
            console.print(
                "\n[dim]429 у бесплатной модели — это занятость общего пула провайдера,\n"
                "а не проблема с ключом. Помогает подождать, взять другую модель\n"
                "(narezka models) или подключить свой ключ провайдера в настройках\n"
                "OpenRouter: https://openrouter.ai/settings/integrations[/dim]"
            )
            raise typer.Exit(1) from None

        console.print(f"[green]Работает.[/green] Ответила [bold]{result['model']}[/bold]: "
                      f"«{result['reply']}»")
        if result["model"] != config.llm.model:
            console.print(
                f"[yellow]Основная модель {config.llm.model} не ответила — сработала запасная.[/yellow]"
            )
        usage = result["usage"]
        if usage:
            # Показываем только счётчики: полный словарь провайдера занимает
            # пять строк и ничего не добавляет.
            console.print(
                f"[dim]токенов: {usage.get('prompt_tokens', '?')} на запрос, "
                f"{usage.get('completion_tokens', '?')} в ответе[/dim]"
            )
        return

    try:
        available = llm.fetch_models(key)
    except llm.LlmError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("[dim]Если сеть за прокси — проверьте ALL_PROXY и HTTPS_PROXY.[/dim]")
        raise typer.Exit(1) from None

    shown = available if all_models else llm.rank_free(available)
    table = Table(title="Бесплатные модели OpenRouter" if not all_models else "Модели OpenRouter")
    table.add_column("")
    table.add_column("Модель", style="bold")
    table.add_column("Контекст", justify="right")
    table.add_column("Ответ по схеме")
    table.add_column("Инструменты")

    for model in shown[:limit]:
        chosen = model.id == config.llm.model
        table.add_row(
            "→" if chosen else "",
            model.id,
            f"{model.context_length // 1000}K" if model.context_length else "?",
            "[green]да[/green]" if model.structured else "[yellow]нет[/yellow]",
            "да" if model.tools else "нет",
        )
    console.print(table)
    console.print(
        f"[dim]Всего моделей {len(available)}, из них бесплатных "
        f"{sum(1 for m in available if m.is_free)}. "
        f"Порядок: сначала те, что умеют отвечать по схеме JSON.[/dim]"
    )
    if not key:
        console.print(f"[yellow]Ключа нет.[/yellow] Положите его в .env как {llm.API_KEY_ENV}.")
    if not config.llm.model:
        console.print("[yellow]Модель не выбрана.[/yellow] Впишите её в configs/config.yaml → llm.model")


def _load_selection(paths) -> tuple[list[dict], dict]:
    """Отобранные клипы и метаданные отбора."""
    artifact = Artifact(paths.analysis / "selection.json")
    if not artifact.exists():
        console.print("[red]Отбор не выполнен[/red] — сначала narezka run llm_select")
        raise typer.Exit(1)
    data = artifact.read_json()
    return data.get("clips", []), data


def _title_for(paths, index: int) -> str | None:
    artifact = Artifact(paths.analysis / "publish.json")
    if not artifact.exists():
        return None
    try:
        for entry in artifact.read_json().get("clips", []):
            if entry.get("index") == index:
                return entry.get("title")
    except (ValueError, KeyError):
        pass
    return None


@app.command()
def publish(
    video_id: Annotated[str, typer.Option("--video-id", "-i")],
    clip: Annotated[int, typer.Option("--clip", "-c", help="Номер клипа из обзора")],
    platform: Annotated[str, typer.Option("--platform", help="youtube | tiktok | instagram | vk")],
    url: Annotated[str | None, typer.Option("--url", help="Ссылка на публикацию")] = None,
    at: Annotated[str | None, typer.Option("--at", help="Дата публикации, ISO; по умолчанию сейчас")] = None,
    project: Annotated[str, typer.Option("--project", "-p")] = "default",
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Отметить клип опубликованным, заморозив его вектор признаков.

    Именно в этот момент признаки перестают меняться: дальше стадии можно
    пересчитывать сколько угодно, а запись в базе останется той, при которой
    клип ушёл в публикацию (§63).
    """
    from narezka.core import db, review  # noqa: PLC0415

    config = load_config(config_path)
    paths = video_paths(config.storage_root, project, video_id)
    if not paths.exists():
        console.print(f"[red]Видео '{video_id}' не найдено.[/red]")
        raise typer.Exit(1)

    clips, selection = _load_selection(paths)
    chosen = next((c for c in clips if c.get("index") == clip), None)
    if chosen is None:
        available = ", ".join(str(c.get("index")) for c in clips)
        console.print(f"[red]Клипа {clip} нет в отборе.[/red] Доступны: {available}")
        raise typer.Exit(1)

    # Решение человека и его правка границ — тоже обучающий сигнал (§63).
    # Сдвиг считается относительно того, что предложила система, а не
    # относительно сырого кандидата: систематическую ошибку показывает
    # именно поправка человека.
    verdict = None
    shift: tuple[float | None, float | None] = (None, None)
    review_artifact = Artifact(paths.review)
    if review_artifact.exists():
        try:
            entry = review.by_index(review_artifact.read_json()).get(clip)
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

    with db.connect(config.storage_root) as connection:
        db.upsert_video(
            connection,
            video_id=video_id,
            project_id=project,
            title=metadata.get("source_title") or metadata.get("source_file"),
            content_origin=metadata.get("content_origin", "own"),
            retention_until=metadata.get("retention_until"),
        )
        clip_id = db.freeze_clip(
            connection,
            clip={**chosen, "prompt_version": selection.get("prompt_version")},
            video_id=video_id,
            weights=selection.get("weights"),
            title=_title_for(paths, clip),
            platform=platform,
            url=url,
            published_at=at or db.now(),
            human_verdict=verdict,
            bounds_shift=shift,
        )

    console.print(f"[green]Зафиксирован[/green] клип [bold]{clip_id}[/bold] на площадке {platform}")
    console.print(f"Дальше внесите метрики: [bold]narezka metrics --clip-id {clip_id} --views ...[/bold]")


@app.command()
def metrics(
    clip_id: Annotated[str, typer.Option("--clip-id", help="Идентификатор из narezka publish")],
    views: Annotated[int | None, typer.Option("--views")] = None,
    likes: Annotated[int | None, typer.Option("--likes")] = None,
    comments: Annotated[int | None, typer.Option("--comments")] = None,
    shares: Annotated[int | None, typer.Option("--shares")] = None,
    retention: Annotated[float | None, typer.Option("--retention", help="Доля досмотра, 0..1")] = None,
    ctr: Annotated[float | None, typer.Option("--ctr", help="Кликабельность, 0..1")] = None,
    platform: Annotated[str | None, typer.Option("--platform")] = None,
    at: Annotated[str | None, typer.Option("--at", help="Когда сняты метрики, ISO")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Внести фактические показатели опубликованного клипа.

    Замеры копятся, а не перезаписываются: тысяча просмотров за сутки и та же
    тысяча за месяц — разные результаты, и историю роста видно только так.
    """
    from narezka.core import db  # noqa: PLC0415

    config = load_config(config_path)
    with db.connect(config.storage_root) as connection:
        row = connection.execute(
            "SELECT platform FROM clips WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        if row is None:
            console.print(f"[red]Клип {clip_id} не зафиксирован.[/red] Сначала narezka publish")
            raise typer.Exit(1)
        try:
            db.add_measurement(
                connection,
                clip_id=clip_id,
                platform=platform or row["platform"] or "other",
                measured_at=at,
                views=views, likes=likes, comments=comments,
                shares=shares, retention=retention, ctr=ctr, note=note,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None

    console.print(f"[green]Записано.[/green] Отчёт: [bold]narezka report[/bold]")


@app.command()
def report(
    video_id: Annotated[str | None, typer.Option("--video-id", "-i")] = None,
    metric: Annotated[str, typer.Option("--metric", help="views | likes | retention | ctr")] = "views",
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Связка «вектор признаков → фактический результат» (§63)."""
    from narezka.core import db, feedback  # noqa: PLC0415

    config = load_config(config_path)
    with db.connect(config.storage_root) as connection:
        rows = db.clip_rows(connection, video_id)

    if not rows:
        console.print("[yellow]Данных пока нет.[/yellow] Опубликуйте клип: narezka publish --help")
        return

    data = feedback.report(rows, metric)
    console.print(
        f"Клипов [bold]{data['clips']}[/bold], опубликовано {data['published']}, "
        f"с метриками {data['measured']}"
    )

    verdicts = data["verdicts"]
    if verdicts["gap"] is not None:
        style = "green" if verdicts["gap"] > 0.1 else "yellow"
        console.print(
            f"\nСогласие с человеком: принятые [bold]{verdicts['mean_accepted']}[/bold] "
            f"против отклонённых [bold]{verdicts['mean_rejected']}[/bold], "
            f"разрыв [{style}]{verdicts['gap']:+.3f}[/{style}]"
        )
        if not verdicts["reliable"]:
            console.print("[dim]Наблюдений мало — числа пока ни о чём не говорят.[/dim]")

    bounds = data["bounds"]
    if bounds["mean_start_shift"] is not None:
        console.print(
            f"\nПравка границ: начало в среднем {bounds['mean_start_shift']:+.2f} с, "
            f"конец {bounds['mean_end_shift']:+.2f} с"
        )

    correlations = data["correlations"]
    status = feedback.correlation_status(correlations)
    if status == "no_data":
        console.print(f"\n[dim]Метрики «{metric}» ещё не внесены.[/dim]")
        return
    if status == "single_point":
        console.print(
            f"\n[dim]Клип с метриками пока один. Связь считается от двух, "
            f"а осмысленной становится от {correlations['min_sample']}.[/dim]"
        )
        return

    table = Table(title=f"Связь факторов с «{metric}»")
    table.add_column("Фактор", style="bold")
    table.add_column("Связь", justify="right")
    table.add_column("Наблюдений", justify="right")
    for name, entry in sorted(
        correlations["factors"].items(),
        key=lambda item: abs(item[1]["correlation"] or 0),
        reverse=True,
    ):
        value = entry["correlation"]
        table.add_row(name, "—" if value is None else f"{value:+.3f}", str(entry["sample"]))
    console.print(table)

    if not correlations["reliable"]:
        console.print(
            f"[yellow]Наблюдений {correlations['sample']}, надёжно от "
            f"{correlations['min_sample']}.[/yellow] На таком объёме коэффициент "
            "скачет от добавления одной строки — принимать решения по нему рано."
        )


@app.command()
def status(
    video_id: Annotated[str | None, typer.Option("--video-id", "-i")] = None,
    project: Annotated[str, typer.Option("--project", "-p")] = "default",
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Состояние видео и стадий."""
    config = load_config(config_path)

    if video_id is None:
        ids = list_videos(config.storage_root, project)
        if not ids:
            console.print(f"В проекте '{project}' пока нет видео.")
            return
        table = Table(title=f"Проект {project}")
        table.add_column("video_id", style="bold")
        table.add_column("Длительность")
        table.add_column("Разрешение")
        for vid in ids:
            paths = video_paths(config.storage_root, project, vid)
            meta = Artifact(paths.metadata)
            data = meta.read_json() if meta.exists() else {}
            duration = data.get("duration_seconds")
            video = data.get("video") or {}
            table.add_row(
                vid,
                f"{duration / 60:.1f} мин" if duration else "—",
                f"{video.get('width')}x{video.get('height')}" if video.get("width") else "—",
            )
        console.print(table)
        return

    paths = video_paths(config.storage_root, project, video_id)
    if not paths.exists():
        console.print(f"[red]Видео '{video_id}' не найдено.[/red]")
        raise typer.Exit(1)

    from narezka.core import cache  # noqa: PLC0415 — избегаем цикла импортов на уровне модуля

    table = Table(title=f"Видео {video_id}")
    table.add_column("Стадия", style="bold")
    table.add_column("Состояние")
    table.add_column("Когда")
    table.add_column("Время")

    for stage in PIPELINE:
        state_file = Artifact(paths.stage_state / f"{stage.name}.json")
        if not state_file.exists():
            table.add_row(stage.name, "[dim]не запускалась[/dim]", "", "")
            continue
        data = state_file.read_json()
        table.add_row(
            stage.name,
            "[green]выполнена[/green]",
            data.get("finished_at", ""),
            f"{data.get('duration_seconds', 0):.1f} с",
        )
    console.print(table)
    _ = cache  # состояние читается напрямую; модуль импортирован для явной связи

    cost = Artifact(paths.cost)
    if cost.exists():
        data = cost.read_json()
        total = sum(entry.get("seconds_total", 0) for entry in data.get("stages", {}).values())
        console.print(f"\nСуммарное время обработки: [bold]{total:.1f} с[/bold]")


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Перезапуск при правке кода")] = False,
) -> None:
    """Поднять сайт: интерфейс и API на одном адресе.

    Если фронтенд собран, он отдаётся отсюда же — одной команды достаточно.
    Пока не собран, поднимается только API, а рядом печатается, что сделать.
    """
    import uvicorn  # noqa: PLC0415

    from narezka.api.app import mount_frontend  # noqa: PLC0415

    address = f"http://{host}:{port}"
    if mount_frontend():
        console.print(f"Сайт: [bold]{address}[/bold]  ·  API там же, документация: {address}/docs")
    else:
        console.print(f"API: [bold]{address}[/bold]  ·  документация: {address}/docs")
        console.print(
            "[yellow]Интерфейс не собран.[/yellow] Соберите один раз — "
            "[bold]cd frontend && npm install && npm run build[/bold] — "
            "и он будет открываться по тому же адресу.\n"
            "[dim]Для разработки фронтенда: cd frontend && npm run dev (порт 5173, "
            "правки видны сразу).[/dim]"
        )

    uvicorn.run("narezka.api.app:app", host=host, port=port, reload=reload, log_level="warning")


@app.command()
def prune(
    video_id: Annotated[str | None, typer.Option("--video-id", "-i", help="По умолчанию все")] = None,
    project: Annotated[str, typer.Option("--project", "-p")] = "default",
    apply: Annotated[bool, typer.Option("--apply", help="Действительно удалить")] = False,
    include_source: Annotated[bool, typer.Option("--source", help="Удалять и исходники")] = False,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Освободить место в хранилище (§65).

    По умолчанию только показывает, что можно удалить и чем это восстановимо.
    Удаление требует явного --apply: исходник восьмичасовой записи скачивается
    заново десятки минут, и делать это по неосторожности не стоит.
    """
    from narezka.core import retention  # noqa: PLC0415

    config = load_config(config_path)
    targets = [video_id] if video_id else list_videos(config.storage_root, project)
    if not targets:
        console.print("[yellow]В проекте нет видео.[/yellow]")
        return

    candidates = []
    for current in targets:
        paths = video_paths(config.storage_root, project, current)
        if paths.exists():
            candidates.extend(retention.inspect(paths, current))

    if not include_source:
        # Исходник удаляется только по явной просьбе: остальное восстановимо
        # за секунды, а он — минутами скачивания.
        candidates = [c for c in candidates if c.kind != "source"]

    if not candidates:
        console.print("[green]Освобождать нечего.[/green]")
        if not include_source:
            console.print("[dim]Исходники не рассматривались — добавьте --source.[/dim]")
        return

    table = Table(title="Можно освободить" if not apply else "Освобождено")
    table.add_column("Видео", style="bold")
    table.add_column("Что")
    table.add_column("Размер", justify="right")
    table.add_column("Восстановимо")

    freed = 0
    for candidate in sorted(candidates, key=lambda c: -c.size_bytes):
        if apply:
            freed += retention.free(candidate)
        table.add_row(
            candidate.video_id, candidate.kind,
            f"{candidate.size_gb:.2f} ГБ", candidate.recoverable,
        )
    console.print(table)

    total = retention.summarize(candidates)
    if apply:
        console.print(f"[green]Освобождено {freed / 1024**3:.2f} ГБ.[/green]")
    else:
        console.print(
            f"Всего [bold]{total['gb']:.2f} ГБ[/bold]. "
            f"Чтобы удалить: [bold]narezka prune --apply"
            f"{' --source' if include_source else ''}[/bold]"
        )
        for candidate in candidates[:3]:
            console.print(f"[dim]{candidate.video_id} · {candidate.kind}: {candidate.reason}[/dim]")


@app.command()
def clean(
    video_id: Annotated[str, typer.Option("--video-id", "-i")],
    project: Annotated[str, typer.Option("--project", "-p")] = "default",
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Удалить временные файлы прерванных запусков."""
    config = load_config(config_path)
    paths = video_paths(config.storage_root, project, video_id)
    removed = cleanup_partials(paths.base)
    console.print(f"Удалено временных файлов: [bold]{removed}[/bold]")


if __name__ == "__main__":
    app()
