"""Раздача медиафайлов с поддержкой range-запросов.

BAZA.md §69: без range браузер тянет весь файл перед началом воспроизведения.
Для восьмичасового VOD это делает перемотку невозможной, а перемотка —
основа обзора клипов.
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Iterator
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse

CHUNK_SIZE = 1024 * 1024

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


def _guess_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _iter_range(path: Path, start: int, end: int) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def serve_file(path: Path, range_header: str | None) -> FileResponse | StreamingResponse:
    """Отдаёт файл целиком или запрошенный диапазон."""
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"файл не найден: {path.name}")

    size = path.stat().st_size
    media_type = _guess_type(path)

    if not range_header:
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"},
        )

    match = _RANGE.fullmatch(range_header.strip())
    if not match:
        raise HTTPException(status_code=400, detail="некорректный заголовок Range")

    raw_start, raw_end = match.groups()
    if raw_start:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
    else:
        # Суффиксная форма «bytes=-500» — последние N байт.
        if not raw_end:
            raise HTTPException(status_code=400, detail="пустой диапазон")
        start = max(size - int(raw_end), 0)
        end = size - 1

    end = min(end, size - 1)
    if start > end or start >= size:
        raise HTTPException(
            status_code=416,
            detail="диапазон вне файла",
            headers={"Content-Range": f"bytes */{size}"},
        )

    return StreamingResponse(
        _iter_range(path, start, end),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        },
    )
