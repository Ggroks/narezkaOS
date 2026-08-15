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
