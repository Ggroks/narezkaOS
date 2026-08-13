"""Стадия download: получение исходного видео по URL.

BAZA.md §5, §6. YouTube и Twitch VOD через yt-dlp. DRM и приватный доступ
не обходятся: если источник закрыт, стадия честно падает.

Скачивание идёт во временный каталог и переносится целиком только после
успеха — прерванная загрузка не оставляет обрубка, который выглядел бы
готовым исходником (§58).
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from typing import Any

from narezka.core.artifacts import Artifact
from narezka.core.env import free_gb
from narezka.core.media import MEDIA_SUFFIXES
from narezka.core.stage import Device, Stage, StageContext

#: Запас на скачивание. Один длинный VOD занимает 15–30 ГБ (§65).
MIN_FREE_GB = 5.0

TMP_DIR_NAME = ".download.tmp"


class DownloadStage(Stage):
    name = "download"
    version = 1
    device = Device.ANY
    #: Для локального файла скачивать нечего — стадия пропускается,
    #: а не роняет пайплайн.
    optional = True
    description = "Скачивание исходного видео по URL через yt-dlp"

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        record = Artifact(ctx.paths.base / "meta" / "download.json")
        artifacts = [record]
        # Имя файла заранее неизвестно — контейнер зависит от источника.
        # Оно записано в download.json, поэтому проверка кэша видит и медиафайл.
        if record.exists():
            try:
                name = record.read_json().get("file")
            except ValueError:
                name = None
            if name:
                artifacts.append(Artifact(ctx.paths.source / name))
        return artifacts

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        return ctx.config.download.model_dump()

    def check_available(self, ctx: StageContext) -> str | None:
        # Используется Python API, поэтому важна импортируемость модуля,
        # а не наличие бинарника в PATH.
        if importlib.util.find_spec("yt_dlp") is None:
            return "модуль yt_dlp не установлен"
        metadata = Artifact(ctx.paths.metadata)
        if not metadata.exists():
            return "нет metadata.json — видео не зарегистрировано"
        origin = metadata.read_json().get("origin") or {}
        if origin.get("type") != "url":
            return "источник не URL — скачивать нечего"
        free = free_gb(ctx.paths.base)
        if free < MIN_FREE_GB:
            return f"на диске свободно {free:.1f} ГБ, нужно хотя бы {MIN_FREE_GB}"
        return None

    def _format_selector(self, ctx: StageContext) -> str:
        cfg = ctx.config.download
        if cfg.format:
            return cfg.format
        h = cfg.max_height
        # Лучшее видео до max_height плюс лучший звук; запасные варианты —
        # на случай источника без раздельных дорожек.
        return f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"

    def run(self, ctx: StageContext) -> None:
        import yt_dlp  # noqa: PLC0415 — тяжёлый импорт только когда стадия реально работает

        metadata = Artifact(ctx.paths.metadata)
        origin = metadata.read_json()["origin"]
        url = origin["url"]

        tmp_dir = ctx.paths.source / TMP_DIR_NAME
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)

        options = {
            "format": self._format_selector(ctx),
            "merge_output_format": ctx.config.download.merge_format,
            "outtmpl": str(tmp_dir / "source.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "retries": 3,
            "logger": _YtdlpLogger(ctx),
        }

        ctx.log.info("скачиваю %s", url)
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        try:
            downloaded = _single_media_file(tmp_dir)
            final_path = ctx.paths.source / downloaded.name
            shutil.move(str(downloaded), final_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        record: dict[str, Any] = {
            "file": final_path.name,
            "url": url,
            "extractor": info.get("extractor_key"),
            "title": info.get("title"),
            "uploader": info.get("uploader") or info.get("channel"),
            "upload_date": info.get("upload_date"),
            "duration_seconds": info.get("duration"),
            "webpage_url": info.get("webpage_url"),
            "format_selector": options["format"],
        }
        Artifact(ctx.paths.base / "meta" / "download.json").write_json(record)

        # Сведения об источнике полезны при генерации описания (§23).
        existing = metadata.read_json()
        existing["source_title"] = record["title"]
        existing["source_uploader"] = record["uploader"]
        metadata.write_json(existing)

        size_gb = final_path.stat().st_size / 1024**3
        ctx.log.info("сохранено %s (%.2f ГБ)", final_path.name, size_gb)


def _single_media_file(directory: Path) -> Path:
    candidates = [
        p for p in sorted(directory.iterdir())
        if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES and not p.name.endswith(".part")
    ]
    if not candidates:
        listing = ", ".join(p.name for p in sorted(directory.iterdir())) or "(пусто)"
        raise RuntimeError(f"yt-dlp не оставил медиафайла. В каталоге: {listing}")
    # Слияние дорожек может оставить исходные потоки — берём самый крупный файл.
    return max(candidates, key=lambda p: p.stat().st_size)


class _YtdlpLogger:
    """Перенаправляет вывод yt-dlp в наш лог, чтобы не смешивался с консолью."""

    def __init__(self, ctx: StageContext) -> None:
        self._log = ctx.log

    def debug(self, message: str) -> None:
        if message.startswith("[debug] "):
            return
        self._log.debug("%s", message)

    def info(self, message: str) -> None:
        self._log.debug("%s", message)

    def warning(self, message: str) -> None:
        self._log.warning("%s", message)

    def error(self, message: str) -> None:
        self._log.error("%s", message)
