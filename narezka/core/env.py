"""Проверка окружения перед запуском.

BAZA.md §62 и §65: понятное сообщение вместо падения в середине
восьмичасовой обработки. Падение на 90% загрузки из-за переполнения диска —
обидная и полностью предотвратимая ошибка.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from narezka.core.device import DeviceInfo, cpu_count, detect_device

#: Один 8-часовой VOD 1080p60 занимает 15–30 ГБ (§65).
RECOMMENDED_FREE_GB = 40


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    critical: bool = True


#: Схемы прокси, которые понимает httpx (через него ходит huggingface_hub).
KNOWN_PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})

PROXY_ENV_VARS = (
    "ALL_PROXY", "all_proxy",
    "HTTP_PROXY", "http_proxy",
    "HTTPS_PROXY", "https_proxy",
    "FTP_PROXY", "ftp_proxy",
)


def normalize_proxy_env() -> list[str]:
    """Приводит переменные прокси к схемам, которые понимает httpx.

    Практический случай: локальные прокси-клиенты выставляют
    `ALL_PROXY=socks://…`. Большинство инструментов трактует это как SOCKS5,
    но httpx такую схему не знает и падает с «Unknown scheme for proxy URL»
    ещё до первого запроса — то есть модель распознавания просто не скачается.

    Правки действуют только на текущий процесс и его потомков; окружение
    пользовательской оболочки не меняется. Возвращает описания сделанного.
    """
    import os  # noqa: PLC0415

    notes: list[str] = []
    for var in PROXY_ENV_VARS:
        value = os.environ.get(var)
        if not value or "://" not in value:
            continue
        scheme = value.split("://", 1)[0].lower()
        if scheme in KNOWN_PROXY_SCHEMES:
            continue
        if scheme == "socks":
            os.environ[var] = "socks5://" + value.split("://", 1)[1]
            notes.append(f"{var}: socks:// → socks5://")
        else:
            del os.environ[var]
            notes.append(f"{var}: схема {scheme}:// не поддерживается, переменная снята")
    return notes


def _tool_version(binary: str) -> str | None:
    path = shutil.which(binary)
    if not path:
        return None
    try:
        out = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=10, check=False)
        first = out.stdout.splitlines()[0] if out.stdout else ""
        return first or path
    except (subprocess.SubprocessError, OSError):
        return path


def free_gb(path: Path) -> float:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    return shutil.disk_usage(target).free / 1024**3


def available_ram_gb() -> float | None:
    try:
        for line in open("/proc/meminfo", encoding="utf-8"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024**2
    except (OSError, ValueError, IndexError):
        pass
    return None


def run_checks(storage_root: Path, device_setting: str = "auto") -> tuple[list[Check], DeviceInfo | None]:
    checks: list[Check] = []

    version = sys.version_info
    checks.append(
        Check(
            "Python",
            version >= (3, 12),
            f"{version.major}.{version.minor}.{version.micro}",
            critical=True,
        )
    )

    for binary in ("ffmpeg", "ffprobe"):
        found = _tool_version(binary)
        checks.append(Check(binary, found is not None, found or "не найден в PATH"))

    # Модули, а не бинарники: обе библиотеки используются через Python API.
    for module, purpose in (("yt_dlp", "скачивание"), ("faster_whisper", "распознавание")):
        found = importlib.util.find_spec(module) is not None
        checks.append(
            Check(module, found, "установлен" if found else f"не установлен — нужен для: {purpose}", critical=False)
        )

    device: DeviceInfo | None = None
    try:
        device = detect_device(device_setting)
        checks.append(Check("устройство", True, f"{device.kind} — {device.name} ({device.detail})"))
    except RuntimeError as exc:
        checks.append(Check("устройство", False, str(exc)))

    checks.append(Check("ядра CPU", True, str(cpu_count()), critical=False))

    ram = available_ram_gb()
    if ram is not None:
        checks.append(
            Check(
                "доступная RAM",
                ram >= 3,
                f"{ram:.1f} ГБ" + ("" if ram >= 6 else " — стадии запускать последовательно"),
                critical=False,
            )
        )

    disk = free_gb(storage_root)
    checks.append(
        Check(
            "свободно на диске",
            disk >= 5,
            f"{disk:.0f} ГБ"
            + ("" if disk >= RECOMMENDED_FREE_GB else f" — для полного VOD рекомендуется от {RECOMMENDED_FREE_GB} ГБ"),
            critical=False,
        )
    )

    return checks, device


def ensure_ready(storage_root: Path, device_setting: str = "auto") -> DeviceInfo:
    """Бросает исключение, если не выполнено критичное условие."""
    checks, device = run_checks(storage_root, device_setting)
    failed = [c for c in checks if c.critical and not c.ok]
    if failed:
        lines = "\n".join(f"  {c.name}: {c.detail}" for c in failed)
        raise RuntimeError(f"Окружение не готово:\n{lines}\n\nПодробности: narezka doctor")
    assert device is not None
    return device
