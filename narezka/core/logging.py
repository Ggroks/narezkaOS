"""Логирование со структурой: в каждой записи есть video_id и стадия.

BAZA.md §67. Без этого разбор упавшего восьмичасового прогона превращается
в чтение сплошного текста.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_video_id: ContextVar[str] = ContextVar("video_id", default="-")
_stage: ContextVar[str] = ContextVar("stage", default="-")


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.video_id = _video_id.get()
        record.stage = _stage.get()
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s [%(video_id)s/%(stage)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger("narezka")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False


@contextmanager
def log_context(*, video_id: str | None = None, stage: str | None = None) -> Iterator[None]:
    tokens = []
    if video_id is not None:
        tokens.append((_video_id, _video_id.set(video_id)))
    if stage is not None:
        tokens.append((_stage, _stage.set(stage)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def get_logger(name: str = "narezka") -> logging.Logger:
    return logging.getLogger(name if name.startswith("narezka") else f"narezka.{name}")
