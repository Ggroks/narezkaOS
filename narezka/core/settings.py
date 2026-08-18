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

#: Оформление субтитров: имя пресета и правки поверх него.
SUBTITLES_KEY = "subtitles"

#: Ключ правки → поле конфига, по разделам. Имена местами расходятся: в общем
#: списке правок `tag_music` понятнее, чем просто `music`, а `llm_model` — чем
#: `model`. Раздел кадрирования вложен в `output`, поэтому идёт отдельно.
SECTIONS: dict[str, dict[str, str]] = {
    "candidates": {
        key: key
        for key in (
            "use_loudness",
            "use_speech_rate",
            "use_chat",
            "use_chat_reactions",
            "chat_ignore_start",
        )
    },
    "audiotags": {f"tag_{tag}": tag for tag in ("laughter", "music", "shout", "applause", "crowd")},
    "llm": {"llm_model": "model"},
    "detector": {"detector_backend": "backend"},
    "output": {key: key for key in ("encoder", "subtitles_enabled", "loudnorm_enabled")},
}


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


def reset(paths: VideoPaths, keys: set[str]) -> dict[str, Any]:
    """Снимает перечисленные правки, не трогая остальные.

    Сброс кадрирования не должен уносить с собой субтитры и длинную нарезку:
    файл один на запись, а настройки в нём — от разных вкладок.
    """
    stored = {key: value for key, value in load(paths).items() if key not in keys}
    if stored:
        Artifact(paths.framing).write_json(stored)
    else:
        paths.framing.unlink(missing_ok=True)
    return stored


def _merged(defaults: dict[str, Any], stored: dict[str, Any], mapping: dict[str, str]) -> None:
    """Накладывает правки на значения раздела, на месте.

    None означает «не задано вручную», а не «поставить пусто»: интерфейс шлёт
    его для полей, которых человек не трогал.
    """
    for key, field in mapping.items():
        value = stored.get(key)
        if value is not None and field in defaults:
            defaults[field] = value


def effective(config: Config, paths: VideoPaths) -> Config:
    """Конфиг с учётом правок, сделанных для этой записи.

    Применяется один раз при создании контекста стадии, поэтому стадия просто
    читает `ctx.config` и не может забыть про правку. До этого файл настроек
    читали три разных места, каждое знало свой набор ключей — и пять
    переключателей интерфейса сохранялись, показывались сохранёнными, но ни
    на что не влияли.
    """
    return apply(config, load(paths))


def apply(config: Config, stored: dict[str, Any]) -> Config:
    """Накладывает правки на конфиг. Отдельно от чтения — чтобы проверять их
    при сохранении, до записи на диск.

    Негодное значение здесь именно падает, а не откатывается к умолчанию:
    настройка, которая молча не сработала, врёт человеку дважды — на экране
    она выглядит применённой.
    """
    if not stored:
        return config

    data = config.model_dump()
    for section, mapping in SECTIONS.items():
        _merged(data[section], stored, mapping)

    framing = data["output"]["framing"]
    _merged(framing, stored, {key: key for key in framing})

    nested = stored.get(COMPILATION_KEY)
    if isinstance(nested, dict):
        _merged(data["compilation"], nested, {key: key for key in data["compilation"]})

    return Config(**data)


def compilation(config: Config, paths: VideoPaths) -> CompilationConfig:
    """Настройки длинной нарезки с учётом правок для этой записи.

    Собирается через pydantic, а не словарём: правка, пришедшая из интерфейса,
    должна проходить ту же проверку, что и конфиг.
    """
    data = config.compilation.model_dump()
    stored = load(paths).get(COMPILATION_KEY)
    if isinstance(stored, dict):
        _merged(data, stored, {key: key for key in data})
    return CompilationConfig(**data)


def subtitles(config, paths: VideoPaths):
    """Оформление субтитров для этой записи.

    Пресет из конфига, поверх — правки, сделанные для записи. Правки
    хранятся полями, а не готовым стилем: иначе смена пресета затирала бы
    их все, хотя человек менял только цвет.
    """
    from narezka.core import subtitles as subs  # noqa: PLC0415 — избегаем цикла

    stored = load(paths).get(SUBTITLES_KEY)
    stored = stored if isinstance(stored, dict) else {}
    preset = stored.get("preset") or config.subtitles.style
    try:
        return subs.preset_style(preset, stored.get("style"))
    except (KeyError, TypeError):
        # Пресет мог исчезнуть между версиями. Ролик без субтитров хуже,
        # чем ролик с обычными.
        return subs.preset_style(subs.DEFAULT_PRESET, stored.get("style"))
