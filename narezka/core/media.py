"""Вызовы внешних медиа-инструментов.

BAZA.md §66: subprocess без shell=True и без склейки строк. Защита не столько
от злоумышленника, сколько от собственных имён файлов с пробелами, кавычками
и кириллицей.
"""

from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".ts", ".avi", ".flv", ".m4v", ".mpg", ".mpeg"}

#: Источник может быть без картинки: подкаст, выгруженная дорожка, фикстура
#: для проверки распознавания. Стадии, которым нужна картинка, объявляют это
#: сами и корректно пропускаются (§62, мягкая деградация).
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}

MEDIA_SUFFIXES = VIDEO_SUFFIXES | AUDIO_SUFFIXES


class MediaError(RuntimeError):
    pass


def run_tool(
    args: list[str],
    *,
    timeout: int = 600,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Запуск внешнего инструмента списком аргументов, без оболочки.

    `cwd` нужен фильтру субтитров: libass принимает путь как часть строки
    фильтра, и экранирование двоеточий и обратных слэшей там своё. Проще
    перейти в каталог и передать одно имя файла.
    """
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False, cwd=cwd
        )
    except FileNotFoundError as exc:
        raise MediaError(f"не найден исполняемый файл: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{args[0]} не завершился за {timeout} с") from exc
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-5:]
        raise MediaError(f"{args[0]} вернул код {result.returncode}: " + " | ".join(tail))
    return result


def ffprobe(path: Path) -> dict[str, Any]:
    result = run_tool(
        [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=120,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError(f"не удалось разобрать вывод ffprobe для {path.name}") from exc


def parse_fps(rate: str | None) -> float | None:
    """r_frame_rate приходит дробью вида '60000/1001'."""
    if not rate or rate in ("0/0", "N/A"):
        return None
    try:
        value = float(Fraction(rate))
    except (ValueError, ZeroDivisionError):
        return None
    return round(value, 3) if value > 0 else None


def summarize(probe: dict[str, Any]) -> dict[str, Any]:
    """Сводка ffprobe в плоский вид, который нужен пайплайну (§6)."""
    fmt = probe.get("format", {})
    streams = probe.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = fmt.get("duration")
    summary: dict[str, Any] = {
        "duration_seconds": round(float(duration), 3) if duration else None,
        "size_bytes": int(fmt["size"]) if fmt.get("size") else None,
        "container": fmt.get("format_name"),
        "bitrate": int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
        "has_video": video is not None,
        "has_audio": audio is not None,
    }
    if video:
        summary["video"] = {
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": parse_fps(video.get("r_frame_rate")),
            "pix_fmt": video.get("pix_fmt"),
        }
    if audio:
        summary["audio"] = {
            "codec": audio.get("codec_name"),
            "sample_rate": int(audio["sample_rate"]) if audio.get("sample_rate") else None,
            "channels": audio.get("channels"),
        }
    return summary


def find_source(source_dir: Path) -> Path:
    """Единственный медиафайл в source/. Соглашение, на которое опираются стадии."""
    if not source_dir.is_dir():
        raise MediaError(f"нет каталога {source_dir}")
    candidates = [
        p for p in sorted(source_dir.iterdir())
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in MEDIA_SUFFIXES
    ]
    if not candidates:
        raise MediaError(f"в {source_dir} нет медиафайла")
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        raise MediaError(f"в {source_dir} больше одного медиафайла: {names}")
    return candidates[0]
