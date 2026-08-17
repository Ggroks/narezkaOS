"""Командный интерфейс.

BAZA.md §33: каждая операция доступна и из CLI, и (позже) из UI — они работают
поверх одного и того же слоя, без приватных путей.
§58: любая стадия запускается изолированно по video-id над артефактами хранилища.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from narezka.core import accounts, credits, db, env, queue, registry, settings
from narezka.core.artifacts import Artifact, cleanup_partials
from narezka.core.config import load_config
from narezka.core.logging import get_logger, setup_logging
from narezka.core.paths import list_videos, video_paths
from narezka.core.runner import Outcome, run_pipeline, run_stage
from narezka.core.stage import StageContext
from narezka.stages import GROUPS, PIPELINE, REGISTRY, get_stage, stages_for

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
    title: Annotated[str | None, typer.Option("--title", "-t", help="Своё название записи")] = None,
    project: Annotated[str, typer.Option("--project", "-p")] = "default",
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Зарегистрировать видео проекта: локальный файл или URL."""
    config = load_config(config_path)

    if file is not None and not registry.known_suffix(file):
        console.print(f"[yellow]Расширение {file.suffix} не в списке известных — пробую всё равно.[/yellow]")

    try:
        result = registry.register(
            storage_root=config.storage_root, project=project, url=url, file=file, title=title
        )
    except registry.RegistrationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(
        f"[green]{'Добавлено' if result.created else 'Уже было'}[/green] "
        f"«{result.title}» video_id=[bold]{result.video_id}[/bold] ({result.placement})"
    )
    console.print(f"Дальше: [bold]narezka pipeline --video-id {result.video_id}[/bold]")


@app.command()
def rename(
    video_id: Annotated[str, typer.Option("--video-id", "-i")],
    title: Annotated[str, typer.Argument(help="Новое название; пустое вернёт заголовок источника")] = "",
    project: Annotated[str, typer.Option("--project", "-p")] = "default",
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Переименовать запись. То же делает карандаш на карточке в каталоге."""
    config = load_config(config_path)
    paths = video_paths(config.storage_root, project, video_id)
    if not paths.exists():
        console.print(f"[red]Видео '{video_id}' не найдено.[/red]")
        raise typer.Exit(1)
    try:
        current = registry.rename(paths, title)
    except registry.RegistrationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Теперь[/green] «{current}»")


user_app = typer.Typer(help="Учётные записи сервиса (§9A)")
app.add_typer(user_app, name="user")


@user_app.command("add")
def user_add(
    login: Annotated[str, typer.Option("--login", "-l", help="Имя для входа")],
    password: Annotated[str, typer.Option("--password", "-p", prompt=True, hide_input=True)],
    admin: Annotated[bool, typer.Option("--admin", help="Может заводить других")] = False,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Завести учётку. Пространство хранения выдаётся по логину.

    Работает и при выключенном входе: учётки можно завести заранее,
    а включить `auth.enabled` в конфиге потом.
    """
    config = load_config(config_path)
    with db.connect(config.storage_root) as connection:
        try:
            user = accounts.create(
                connection, login=login, password=password, is_admin=admin
            )
        except accounts.AccountError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

    console.print(f"[green]Заведена[/green] «{user.login}», пространство [bold]{user.workspace}[/bold]")
    if not config.auth.enabled:
        console.print("[yellow]Вход выключен: включите auth.enabled в config.yaml.[/yellow]")


@user_app.command("list")
def user_list(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Кто заведён в сервисе."""
    config = load_config(config_path)
    with db.connect(config.storage_root) as connection:
        users = accounts.listing(connection)

    if not users:
        console.print("Учёток нет. Местная работа идёт от хозяина машины.")
        return
    table = Table(title=f"Учётки (вход {'включён' if config.auth.enabled else 'выключен'})")
    table.add_column("#")
    table.add_column("Логин", style="bold")
    table.add_column("Пространство")
    table.add_column("Права")
    for user in users:
        table.add_row(
            str(user.user_id), user.login, user.workspace,
            "хозяин" if user.is_admin else "",
        )
    console.print(table)


invite_app = typer.Typer(help="Приглашения: регистрация закрыта кодом")
app.add_typer(invite_app, name="invite")


@invite_app.command("new")
def invite_new(
    credits_amount: Annotated[
        float, typer.Option("--credits", "-c", help="Сколько кредитов подарить вошедшему")
    ] = 0.0,
    note: Annotated[str | None, typer.Option("--note", "-n", help="Для кого — себе на память")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Выдать код приглашения.

    Ноль кредитов означает «подарить сколько положено по конфигу»
    (`billing.signup_bonus`), а не «ничего»: человек, вошедший с пустым
    счётом, упрётся в отказ, не увидев, ради чего всё затевалось.
    """
    config = load_config(config_path)
    with db.connect(config.storage_root) as connection:
        code = accounts.new_invite(connection, credits=credits_amount, note=note)

    gift = credits_amount or config.billing.signup_bonus
    console.print(f"Код: [bold]{code}[/bold]")
    console.print(f"Подарок при входе: [bold]{gift:.0f}[/bold] кредитов")


@invite_app.command("list")
def invite_list(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Выданные приглашения и кто по ним вошёл."""
    config = load_config(config_path)
    with db.connect(config.storage_root) as connection:
        rows = accounts.invites(connection)

    if not rows:
        console.print("Приглашений нет. Выдать: [bold]narezka invite new[/bold]")
        return
    table = Table(title="Приглашения")
    table.add_column("Код", style="bold")
    table.add_column("Кредитов", justify="right")
    table.add_column("Кому")
    table.add_column("Использовано")
    for row in rows:
        table.add_row(
            row["code"], f"{row['credits']:.0f}", row["note"] or "",
            row["used_by"] or "[dim]ждёт[/dim]",
        )
    console.print(table)


credits_app = typer.Typer(help="Счёт в кредитах")
app.add_typer(credits_app, name="credits")


@credits_app.command("show")
def credits_show(
    workspace: Annotated[str, typer.Option("--workspace", "-w")] = "default",
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Счёт и последние движения."""
    config = load_config(config_path)
    with db.connect(config.storage_root) as connection:
        balance = credits.balance(connection, workspace)
        rows = credits.history(connection, workspace, limit=20)

    console.print(f"Счёт [bold]{workspace}[/bold]: [bold]{balance:.0f}[/bold] кредитов")
    hourly = config.billing.per_video_hour
    console.print(
        "Цены (версия {v}): {rates}".format(
            v=config.billing.version,
            rates=", ".join(f"{name} {price:.0f}/час записи" for name, price in hourly.items()),
        )
    )
    if not rows:
        return

    table = Table(title="Последние движения")
    table.add_column("Когда")
    table.add_column("Что")
    table.add_column("Кредитов", justify="right")
    table.add_column("За что")
    for row in rows:
        sign = "green" if row["amount"] > 0 else "white"
        table.add_row(
            row["at"][:16].replace("T", " "),
            {"topup": "пополнение", "grant": "подарок", "charge": "списание",
             "refund": "возврат"}.get(row["kind"], row["kind"]),
            f"[{sign}]{row['amount']:+.0f}[/{sign}]",
            row["note"] or (f"{row['work_groups']}, {row['video_hours']} ч записи"
                            if row["work_groups"] else ""),
        )
    console.print(table)


@credits_app.command("add")
def credits_add(
    workspace: Annotated[str, typer.Option("--workspace", "-w", help="Кому")],
    amount: Annotated[float, typer.Option("--amount", "-a", help="Сколько кредитов")],
    note: Annotated[str | None, typer.Option("--note", "-n")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Пополнить счёт.

    Пока вручную: приём платежей — отдельная задача, а книга к нему готова.
    Когда он появится, он будет писать те же самые записи.
    """
    config = load_config(config_path)
    with db.connect(config.storage_root) as connection:
        try:
            balance = credits.add(
                connection, workspace=workspace, amount=amount, note=note or "вручную"
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(f"[green]Пополнено[/green] на {amount:.0f}, стало [bold]{balance:.0f}[/bold]")


@app.command()
def usage(
    days: Annotated[int, typer.Option("--days", help="За сколько последних дней")] = 30,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Сколько наработано: часы записи и машинное время.

    Часы записи — то, по чему считается стоимость: замеры показали, что
    себестоимость линейна по ним и ни по чему другому. Машинное время рядом,
    чтобы видеть, во что это обходится на самом деле.
    """
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    config = load_config(config_path)
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    with db.connect(config.storage_root) as connection:
        rows = queue.usage(connection, since=since)
        pending = queue.waiting(connection)

    if not rows:
        console.print(f"За последние {days} дн. обработки не было.")
    else:
        table = Table(title=f"Наработано за {days} дн.")
        table.add_column("Пространство", style="bold")
        table.add_column("Задач", justify="right")
        table.add_column("Часов записи", justify="right")
        table.add_column("Машинного времени", justify="right")
        for row in rows:
            table.add_row(
                row["workspace"], str(row["tasks"]), f"{row['video_hours']:.2f}",
                f"{row['machine_seconds'] / 3600:.2f} ч",
            )
        console.print(table)

        total = sum(row["video_hours"] for row in rows)
        machine = sum(row["machine_seconds"] for row in rows) / 3600
        console.print(
            f"Итого [bold]{total:.2f}[/bold] ч записи, "
            f"машина занята [bold]{machine:.2f}[/bold] ч "
            f"({machine / total:.2f} ч на час записи)" if total else ""
        )

    if pending:
        console.print(f"\nВ очереди сейчас: [bold]{pending}[/bold]")


@app.command()
def stop(
    video_id: Annotated[str, typer.Option("--video-id", help="Какое видео остановить")],
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8000,
) -> None:
    """Остановить обработку, не трогая сервер.

    Остановка происходит после текущей стадии: обрывать её посреди работы
    значит потерять уже посчитанное — незавершённая стадия не кэшируется.
    Всё, что завершено, сохраняется, и повторный запуск продолжит с этого места.
    """
    import httpx  # noqa: PLC0415

    url = f"http://{host}:{port}/api/videos/{video_id}/stop"
    try:
        response = httpx.post(url, timeout=10.0)
    except httpx.HTTPError as exc:
        console.print(f"[red]сервер не отвечает на {host}:{port}: {exc}[/]")
        raise typer.Exit(1) from exc

    if response.status_code == 409:
        console.print("[yellow]обработка не идёт — останавливать нечего[/]")
        raise typer.Exit(1)
    response.raise_for_status()
    console.print("[green]остановка запрошена — прогон завершится после текущей стадии[/]")


@app.command()
def stages() -> None:
    """Показать доступные стадии."""
    table = Table(title="Стадии пайплайна")
    table.add_column("#")
    table.add_column("Стадия", style="bold")
    table.add_column("Кусок работы")
    table.add_column("Устройство")
    table.add_column("Описание")
    for index, stage in enumerate(PIPELINE, 1):
        table.add_row(
            str(index), stage.name, GROUPS.get(stage.group, stage.group),
            stage.device.value, stage.description,
        )
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
    group: Annotated[
        str | None,
        typer.Option("--group", "-g", help="Кусок работы: analysis | shorts | long"),
    ] = None,
    project: Annotated[str, typer.Option("--project", "-p")] = "default",
    force: Annotated[bool, typer.Option("--force", help="Игнорировать кэш")] = False,
    profile: Annotated[str | None, typer.Option("--profile", help="dev | batch")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Прогнать пайплайн целиком или один кусок работы.

    `--group analysis` доводит запись до расшифровки и найденных моментов,
    `shorts` добавляет к этому сборку роликов, `long` — длинную нарезку.
    Недостающее подтягивается само, уже посчитанное берётся из кэша, поэтому
    порядок нажатий значения не имеет.
    """
    ctx = _build_context(video_id, project, profile, config_path)
    console.print(f"Профиль [bold]{ctx.config.resolved_profile}[/bold], устройство [bold]{ctx.device.kind}[/bold]")

    try:
        planned = stages_for(group) if group else list(PIPELINE)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    results = run_pipeline(planned, ctx, force=force)

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
def longcut(
    video_id: Annotated[str, typer.Option("--video-id", "-i")],
    story: Annotated[bool, typer.Option("--story/--no-story", help="Связные эпизоды")] = False,
    best: Annotated[bool, typer.Option("--best/--no-best", help="Подборка лучших моментов")] = False,
    episodes: Annotated[
        str | None,
        typer.Option("--episodes", help="Номера эпизодов через запятую; пусто — все подряд"),
    ] = None,
    minutes: Annotated[int | None, typer.Option("--minutes", help="Желаемая длина, мин")] = None,
    project: Annotated[str, typer.Option("--project", "-p")] = "default",
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Что собирать в длинную нарезку. То же делает вкладка «Длинная нарезка».

    Настройка хранится при записи, а не в общем конфиге: подходящая длина
    и состав зависят от того, что в этой записи происходит.
    """
    config = load_config(config_path)
    paths = video_paths(config.storage_root, project, video_id)
    if not paths.exists():
        console.print(f"[red]Видео '{video_id}' не найдено.[/red]")
        raise typer.Exit(1)

    chosen: list[int] | None = None
    if episodes is not None:
        try:
            chosen = [int(part) for part in episodes.split(",") if part.strip()]
        except ValueError as exc:
            console.print("[red]Номера эпизодов — числа через запятую, например 0,2,5.[/red]")
            raise typer.Exit(1) from exc

    current = settings.compilation(config, paths)
    settings.update(paths, {
        settings.COMPILATION_KEY: {
            "story": story,
            "best": best,
            "episodes": chosen if chosen is not None else current.episodes,
            "target_minutes": minutes if minutes is not None else current.target_minutes,
            # Выбор человека и есть включение: отдельная галочка «собирать»
            # поверх «собрать подборку» была бы вторым выключателем к той же
            # лампе. Поиск эпизодов нужен только сюжетному режиму.
            "enabled": story or best,
            "find_episodes": story,
        }
    })

    updated = settings.compilation(config, paths)
    what = ", ".join(filter(None, [
        "связные эпизоды" if updated.story else "",
        "подборка лучших" if updated.best else "",
    ])) or "ничего"
    console.print(f"Длинная нарезка: [bold]{what}[/bold], около {updated.target_minutes} мин")
    if updated.enabled:
        console.print(f"Собрать: [bold]narezka pipeline --video-id {video_id} --group long[/bold]")


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
    try:
        current = llm.provider(config.llm.provider)
    except llm.LlmError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    key = llm.api_key(provider_name=config.llm.provider)

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
                key, chain, timeout=config.llm.timeout_seconds, on_attempt=report,
                provider_name=config.llm.provider,
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
        available = llm.fetch_models(key, provider_name=config.llm.provider)
    except llm.LlmError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("[dim]Если сеть за прокси — проверьте ALL_PROXY и HTTPS_PROXY.[/dim]")
        raise typer.Exit(1) from None

    shown = available if all_models else llm.rank_free(available)
    table = Table(
        title=f"{'Бесплатные модели' if not all_models else 'Модели'} · {current.name}"
    )
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
        console.print(f"[yellow]Ключа нет.[/yellow] Положите его в .env как {current.key_env}.")
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
            title=registry.display_title(metadata, video_id),
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
        table.add_column("Название")
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
                registry.display_title(data, vid),
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
