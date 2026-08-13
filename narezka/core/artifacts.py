"""Артефакты: атомарная запись и отпечаток для ключа кэша.

BAZA.md §58. Запись идёт во временный файл рядом с целевым, затем переименование.
Прерванная стадия не оставляет полуфайл, который кэш примет за готовый.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

# Файлы меньше порога хэшируются по содержимому — это надёжно и дёшево.
# Крупные (видео в десятки гигабайт) — по размеру и времени изменения:
# полное хэширование заняло бы минуты на каждом запуске.
CONTENT_HASH_LIMIT = 8 * 1024 * 1024


@dataclass(frozen=True)
class Artifact:
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    def exists(self) -> bool:
        return self.path.exists()

    def fingerprint(self) -> str:
        """Отпечаток для ключа кэша. Отсутствующий файл даёт стабильное значение."""
        if not self.path.exists():
            return "absent"
        stat = self.path.stat()
        if stat.st_size <= CONTENT_HASH_LIMIT:
            digest = hashlib.sha256()
            with self.path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return f"sha256:{digest.hexdigest()}"
        return f"size-mtime:{stat.st_size}:{stat.st_mtime_ns}"

    @contextmanager
    def open_write(self, mode: str = "w", **kwargs: Any) -> Iterator[IO[Any]]:
        """Атомарная запись: временный файл → fsync → переименование.

        При исключении временный файл удаляется, целевой остаётся нетронутым.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if "b" not in mode:
            kwargs.setdefault("encoding", "utf-8")

        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
        tmp_path = Path(tmp_name)
        os.close(fd)
        try:
            with tmp_path.open(mode, **kwargs) as handle:
                yield handle
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def write_json(self, data: Any) -> None:
        with self.open_write("w") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")

    def read_json(self) -> Any:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write_bytes(self, data: bytes) -> None:
        with self.open_write("wb") as handle:
            handle.write(data)

    @contextmanager
    def reserve(self) -> Iterator[Path]:
        """Временный путь для инструментов, которые пишут файл сами (FFmpeg, yt-dlp).

        Инструмент пишет по выданному пути; после успешного выхода из блока
        файл атомарно занимает место целевого.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f".{self.path.name}.partial")
        tmp_path.unlink(missing_ok=True)
        try:
            yield tmp_path
            if not tmp_path.exists():
                raise FileNotFoundError(
                    f"Стадия не создала файл по зарезервированному пути: {tmp_path}"
                )
            os.replace(tmp_path, self.path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise


def cleanup_partials(directory: Path) -> int:
    """Удаляет мусор от прерванных запусков. Возвращает число удалённых файлов."""
    if not directory.is_dir():
        return 0
    removed = 0
    for entry in directory.rglob(".*"):
        if entry.is_file() and (entry.name.endswith(".partial") or entry.name.endswith(".tmp")):
            entry.unlink(missing_ok=True)
            removed += 1
    return removed
