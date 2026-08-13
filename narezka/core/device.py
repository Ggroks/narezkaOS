"""Определение вычислительного устройства.

BAZA.md §62: программа не пишется под конкретный процессор или видеокарту.
CPU — всегда работающий путь; ускоритель используется, если он есть.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

DeviceKind = Literal["cpu", "cuda"]


@dataclass(frozen=True)
class DeviceInfo:
    kind: DeviceKind
    name: str
    detail: str = ""

    @property
    def is_accelerator(self) -> bool:
        return self.kind != "cpu"


def _detect_cuda() -> DeviceInfo | None:
    """CUDA через torch, если он установлен, иначе через nvidia-smi.

    Torch импортируется лениво: на этапе 0 его в зависимостях нет,
    и отсутствие не должно ломать определение устройства.
    """
    try:
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            return DeviceInfo("cuda", torch.cuda.get_device_name(0), "определено через torch")
    except Exception:  # noqa: BLE001 — torch может отсутствовать или упасть при инициализации
        pass

    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            name = out.stdout.strip().splitlines()
            if out.returncode == 0 and name:
                return DeviceInfo("cuda", name[0].strip(), "определено через nvidia-smi")
        except (subprocess.SubprocessError, OSError):
            pass

    return None


def _cpu_name() -> str:
    try:
        for line in open("/proc/cpuinfo", encoding="utf-8"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "CPU"


def detect_device(setting: str = "auto") -> DeviceInfo:
    """Возвращает устройство по настройке. Явное значение переопределяет поиск."""
    if setting == "cpu":
        return DeviceInfo("cpu", _cpu_name(), "задано явно")
    if setting == "cuda":
        found = _detect_cuda()
        if found:
            return found
        raise RuntimeError(
            "device=cuda задано явно, но CUDA не найдена. "
            "Уберите настройку или используйте device=auto."
        )

    return _detect_cuda() or DeviceInfo("cpu", _cpu_name(), "ускоритель не найден")


def cpu_count() -> int:
    return os.cpu_count() or 1
