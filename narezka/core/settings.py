"""Настройки, заданные для одной записи.

Общий конфиг описывает, как работать вообще; здесь лежит то, что человек
поправил для конкретной записи из интерфейса. Разделение не формальное:
подходящее кадрирование зависит от того, что снято, длина нарезки — от того,
что в записи происходит, а сигналы отбора — от того, есть ли вообще чат.

Файл один на запись — `meta/framing.json`. Имя историческое: сначала в нём
жило только кадрирование. Переименовывать его значит терять настройки всех
уже заведённых записей ради красоты, поэтому имя осталось, а смысл здесь
описан.

Правки хранятся отдельно от расчётов и от конфига, поэтому их всегда можно
снять и вернуться к тому, что предлагает программа.
"""

from __future__ import annotations

from typing import Any

from narezka.core.artifacts import Artifact
from narezka.core.config import CompilationConfig, Config
from narezka.core.paths import VideoPaths

#: Настройки длинной нарезки лежат вложенным словарём, а не полями с
#: приставкой: их пять, и в плоском виде они терялись бы среди двух десятков
#: переключателей сборки.
COMPILATION_KEY = "compilation"


def load(paths: VideoPaths) -> dict[str, Any]:
    """Правки для этой записи. Пустой словарь — ничего не задано вручную."""
    artifact = Artifact(paths.framing)
    if not artifact.exists():
        return {}
    try:
        stored = artifact.read_json()
    except ValueError:
        # Битый файл настроек не должен закрывать доступ к записи: работать
        # по умолчанию хуже, чем по своим настройкам, но лучше, чем никак.
        return {}
    return stored if isinstance(stored, dict) else {}


def update(paths: VideoPaths, patch: dict[str, Any]) -> dict[str, Any]:
    """Дописывает правки, не трогая остальные."""
    stored = load(paths)
    stored.update(patch)
    Artifact(paths.framing).write_json(stored)
    return stored


def compilation(config: Config, paths: VideoPaths) -> CompilationConfig:
    """Настройки длинной нарезки с учётом правок для этой записи.

    Собирается через pydantic, а не словарём: правка, пришедшая из интерфейса,
    должна проходить ту же проверку, что и конфиг.
    """
    data = config.compilation.model_dump()
    stored = load(paths).get(COMPILATION_KEY)
    if isinstance(stored, dict):
        data.update({
            key: value for key, value in stored.items()
            if key in data and value is not None
        })
    return CompilationConfig(**data)
