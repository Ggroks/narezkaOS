"""Освобождение места в хранилище.

BAZA.md §65. Исходное видео — единственный крупный потребитель диска:
восьмичасовая запись занимает 15–30 ГБ, всё остальное вместе взятое —
десятки мегабайт. Поэтому речь идёт почти исключительно о нём.

Главное правило безопасности: **удалять можно только то, что восстановимо
или больше не нужно**. Исходник, скачанный по ссылке, восстановим повторным
скачиванием; исходник, добавленный локальным файлом, — нет, он лежит
у пользователя и в хранилище только ссылкой. Различать эти случаи
обязательно, иначе однажды удалится единственная копия записи.
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import time
from pathlib import Path
from typing import Any

from narezka.core.artifacts import Artifact

#: Стадии, которым исходник нужен. Пока хоть одна из них не отработала,
#: удалять его рано: работа встанет.
STAGES_NEEDING_SOURCE = ("probe", "extract_audio", "render")


@dataclass(frozen=True)
class Candidate:
    """Что можно освободить у одного видео."""

    video_id: str
    kind: str                 # source | audio | clips
    path: Path
    size_bytes: int
    #: Можно ли получить это обратно и какой ценой.
    recoverable: str
    reason: str

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / 1024**3, 3)


def _tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        # Символическая ссылка на чужой файл места не занимает: считать её
        # размером значит обещать освободить чужие гигабайты.
        return 0 if path.is_symlink() else path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())


def _stage_done(paths, name: str) -> bool:
    return Artifact(paths.stage_state / f"{name}.json").exists()


def inspect(paths, video_id: str) -> list[Candidate]:
    """Что у этого видео можно освободить прямо сейчас."""
    found: list[Candidate] = []

    metadata = Artifact(paths.metadata)
    origin = {}
    if metadata.exists():
        try:
            origin = metadata.read_json().get("origin") or {}
        except ValueError:
            origin = {}

    pending = [name for name in STAGES_NEEDING_SOURCE if not _stage_done(paths, name)]
    source_size = _tree_size(paths.source)

    if source_size > 0:
        if pending:
            reason = "рано: не отработали стадии " + ", ".join(pending)
            recoverable = "нет"
        elif origin.get("type") == "url":
            reason = "ролики собраны, исходник скачается заново при надобности"
            recoverable = "скачиванием"
        else:
            # Локальный файл лежит у пользователя, в хранилище он ссылкой
            # или копией. Удалять копию можно, но восстановить её мы не сможем.
            reason = "добавлен локальным файлом — удаляется только копия в хранилище"
            recoverable = "из исходного файла пользователя"

        if not pending:
            found.append(Candidate(video_id, "source", paths.source, source_size, recoverable, reason))

    # Звук пересчитывается из исходника за секунды, но только пока исходник
    # на месте. Поэтому предлагается к удалению вместе с ним, а не вместо.
    audio_size = _tree_size(paths.audio)
    if audio_size > 0 and _stage_done(paths, "render"):
        found.append(
            Candidate(
                video_id, "audio", paths.audio, audio_size,
                "пересчётом из исходника",
                "нужен только для распознавания и отбора, они уже отработали",
            )
        )

    # §65: промежуточные нарезки удаляются после сборки финального ролика.
    clips_size = _tree_size(paths.clips)
    if clips_size > 0 and _stage_done(paths, "render"):
        found.append(
            Candidate(video_id, "clips", paths.clips, clips_size, "пересборкой",
                      "промежуточные нарезки, финальные ролики собраны")
        )

    return found


def free(candidate: Candidate) -> int:
    """Удаляет содержимое, оставляя сам каталог.

    Каталог нужен: раскладка хранилища создаётся один раз (§32), и стадии
    рассчитывают, что он на месте.
    """
    import shutil  # noqa: PLC0415

    freed = candidate.size_bytes
    if candidate.path.is_dir():
        for child in candidate.path.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    else:
        candidate.path.unlink(missing_ok=True)
    return freed


def summarize(candidates: list[Candidate]) -> dict[str, Any]:
    total = sum(c.size_bytes for c in candidates)
    return {
        "count": len(candidates),
        "bytes": total,
        "gb": round(total / 1024**3, 3),
        "by_kind": {
            kind: round(sum(c.size_bytes for c in candidates if c.kind == kind) / 1024**3, 3)
            for kind in sorted({c.kind for c in candidates})
        },
    }


# --- автоматическая уборка (§65) --------------------------------------------

#: Что удаляется само. Только восстановимое: скачанный исходник вернётся
#: скачиванием, звук пересчитается из него, промежуточные нарезки соберутся
#: заново. Готовые ролики не трогаются никогда — их человек и ждал.
AUTO_KINDS = ("source", "audio", "clips")


def _age_days(path: Path) -> float:
    """Сколько дней содержимому. По самому свежему файлу внутри."""
    newest = 0.0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                newest = max(newest, item.stat().st_mtime)
            except OSError:
                continue
    if newest == 0.0:
        return 0.0
    return (time.time() - newest) / 86400


def _busy(storage_root: Path) -> set[tuple[str, str]] | None:
    """Записи, по которым есть живая задача. None — узнать не удалось.

    Уборка идёт отдельным потоком и раз в несколько часов, а работа может
    начаться в любую секунду. Без этой проверки исходник исчезал бы прямо
    во время сборки — по инструкции для сервера уборка включена.
    """
    from narezka.core import db, queue  # noqa: PLC0415

    try:
        with db.connect(storage_root) as connection:
            return queue.active_pairs(connection)
    except sqlite3.Error:
        return None


def sweep(storage_root: Path, config, *, apply: bool = False) -> dict[str, Any]:
    """Обходит хранилище и убирает отлежавшееся.

    Возвращает отчёт независимо от `apply`: посмотреть, что будет удалено,
    можно не удаляя. Это то же правило, что у ручной команды — она тоже
    показывает, а удаляет только по явному согласию.
    """
    from narezka.core.paths import list_videos, video_paths  # noqa: PLC0415

    days = config.retention.source_days
    report: dict[str, Any] = {"days": days, "freed_bytes": 0, "items": []}
    if days <= 0:
        return report

    projects_dir = Path(storage_root) / "projects"
    if not projects_dir.is_dir():
        return report

    busy = _busy(storage_root)
    if busy is None:
        # Не знаем, что сейчас в работе. Молча удалять вслепую нельзя:
        # потерять исходник хуже, чем не освободить место.
        report["skipped"] = "не удалось прочитать очередь — уборка отложена"
        return report

    for project in sorted(p.name for p in projects_dir.iterdir() if p.is_dir()):
        for video_id in list_videos(storage_root, project):
            if (project, video_id) in busy:
                # Запись отлежалась, но по ней прямо сейчас идёт работа:
                # человек открыл старый проект и нажал «собрать». Убрать из
                # под неё исходник значит уронить работу на середине.
                continue
            paths = video_paths(storage_root, project, video_id)
            for candidate in inspect(paths, video_id):
                if candidate.kind not in AUTO_KINDS:
                    continue
                # Своё, добавленное файлом, восстановить нечем: у нас нет
                # ни ссылки, ни копии у человека под рукой.
                if candidate.recoverable.startswith("из исходного файла") and not (
                    config.retention.include_uploads
                ):
                    continue
                if _age_days(candidate.path) < days:
                    continue

                report["items"].append({
                    "project": project,
                    "video_id": video_id,
                    "kind": candidate.kind,
                    "bytes": candidate.size_bytes,
                    "recoverable": candidate.recoverable,
                })
                report["freed_bytes"] += candidate.size_bytes
                if apply:
                    free(candidate)

    report["freed_gb"] = round(report["freed_bytes"] / 1024**3, 2)
    return report
