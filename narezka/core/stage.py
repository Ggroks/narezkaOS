"""Интерфейс стадии.

BAZA.md §43 и §58. Стадия — чистая функция «артефакты на входе → артефакты
на выходе», без скрытого состояния. Она объявляет свои входы, выходы, требование
к устройству и ту часть конфигурации, которая влияет на результат.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from narezka.core.artifacts import Artifact
from narezka.core.config import Config
from narezka.core.device import DeviceInfo
from narezka.core.paths import VideoPaths


class Device(StrEnum):
    ANY = "any"
    CPU = "cpu"
    GPU = "gpu"


@dataclass
class StageContext:
    project_id: str
    video_id: str
    paths: VideoPaths
    config: Config
    device: DeviceInfo
    log: logging.Logger
    #: Сообщить о ходе долгой стадии: сделано из скольких и что именно.
    #: Долгие стадии без этого выглядят зависшими — на пятичасовой записи
    #: отбор молчал 43 минуты, и понять, идёт ли работа, было нечем.
    #: None — запуск без наблюдателя (CLI без задачи), вызов ничего не стоит.
    on_progress: Callable[[int, int, str], None] | None = None

    def progress(self, done: int, total: int, note: str = "") -> None:
        """Отметить продвижение. Безопасно вызывать всегда."""
        if self.on_progress is not None:
            self.on_progress(done, total, note)


class StageSkipped(Exception):
    """Стадия неприменима на этой машине или к этому материалу.

    Не ошибка: pipeline продолжается, в отчёт попадает причина (§62,
    мягкая деградация).
    """


class Stage(ABC):
    name: ClassVar[str]
    #: Версия кода стадии. Увеличивать при изменении логики — входит в ключ кэша,
    #: иначе после правки алгоритма будет отдан устаревший результат.
    version: ClassVar[int] = 1
    device: ClassVar[Device] = Device.ANY
    #: Опциональная стадия пропускается при нехватке ресурсов вместо падения.
    optional: ClassVar[bool] = False
    description: ClassVar[str] = ""

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        return []

    @abstractmethod
    def outputs(self, ctx: StageContext) -> list[Artifact]: ...

    @abstractmethod
    def run(self, ctx: StageContext) -> None: ...

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        """Часть конфигурации, влияющая на результат — входит в ключ кэша.

        Возвращать только то, что действительно меняет выход: иначе правка
        несвязанной настройки будет впустую сбрасывать кэш.
        """
        return {}

    def check_available(self, ctx: StageContext) -> str | None:
        """Причина, по которой стадия сейчас невыполнима, либо None.

        Возврат строки при optional=True приводит к пропуску с предупреждением,
        при optional=False — к ошибке.
        """
        if self.device is Device.GPU and not ctx.device.is_accelerator:
            return "требуется ускоритель, доступен только CPU"
        return None
