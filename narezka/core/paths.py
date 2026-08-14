"""Раскладка хранилища — единственный канонический источник путей.

BAZA.md §32. Ни одна стадия не собирает пути вручную: всё через VideoPaths,
иначе артефакты расползаются и кэш перестаёт находить входы.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SUBDIRS = (
    "source",
    "audio",
    "transcript",
    "analysis",
    "edl",
    "clips",
    "shorts",
    "compilations",
    "exports",
    "meta",
)


@dataclass(frozen=True)
class VideoPaths:
    root: Path
    project_id: str
    video_id: str

    @property
    def base(self) -> Path:
        return self.root / "projects" / self.project_id / "videos" / self.video_id

    def __getattr__(self, name: str) -> Path:
        # source, audio, transcript, ... — как атрибуты, без длинных выражений в стадиях.
        if name in SUBDIRS:
            return self.base / name
        raise AttributeError(name)

    @property
    def stage_state(self) -> Path:
        """Состояние кэша стадий: meta/stages/<имя>.json."""
        return self.base / "meta" / "stages"

    @property
    def metadata(self) -> Path:
        return self.base / "meta" / "metadata.json"

    @property
    def framing(self) -> Path:
        """Настройки кадрирования, заданные для этого видео вручную (§61)."""
        return self.base / "meta" / "framing.json"

    @property
    def review(self) -> Path:
        """Решения человека по кандидатам — «годится» / «не годится» (§35, §63)."""
        return self.base / "analysis" / "review.json"

    @property
    def cost(self) -> Path:
        return self.base / "meta" / "cost.json"

    def ensure(self) -> None:
        for name in SUBDIRS:
            (self.base / name).mkdir(parents=True, exist_ok=True)
        self.stage_state.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.base.is_dir()


def video_paths(storage_root: Path, project_id: str, video_id: str) -> VideoPaths:
    return VideoPaths(root=storage_root, project_id=project_id, video_id=video_id)


def list_videos(storage_root: Path, project_id: str) -> list[str]:
    videos_dir = storage_root / "projects" / project_id / "videos"
    if not videos_dir.is_dir():
        return []
    return sorted(p.name for p in videos_dir.iterdir() if p.is_dir())
