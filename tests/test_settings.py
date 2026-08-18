"""Настройки, заданные для одной записи.

Файл настроек один на запись, а вкладок, которые в него пишут, несколько.
Здесь проверяется то, что однажды уже сломалось: правка обязана доходить до
стадии, а сохранение своей вкладки — не уносить чужие настройки.
"""

from __future__ import annotations

import pytest

from narezka.core import settings
from narezka.core.config import FramingConfig, load_config
from narezka.core.device import DeviceInfo
from narezka.core.logging import get_logger
from narezka.core.paths import video_paths
from narezka.core.stage import StageContext

#: Значение, отличное от умолчания, — для правок, которые не переключатели.
#: Переключатель проверка выставляет наоборот сама, а вот новая правка со
#: значением из списка без строки здесь роняет тест: это единственный способ
#: не завести очередной переключатель, который сохраняется и не работает.
OTHER_VALUE: dict[str, object] = {
    "llm_model": "другой/поставщик",
    "detector_backend": "yolox",
    "encoder": "gpu",
}

ALL_OVERRIDES = [key for mapping in settings.SECTIONS.values() for key in mapping]


@pytest.fixture
def paths(tmp_path):
    created = video_paths(tmp_path, "default", "видео")
    created.ensure()
    return created


def context(paths) -> StageContext:
    """Контекст, собранный заново — так делают и воркер, и API на каждый запуск."""
    return StageContext(
        project_id="default", video_id="видео", paths=paths,
        config=load_config(), device=DeviceInfo(kind="cpu", name="test"),
        log=get_logger("test"),
    )


def value_of(config, key: str):
    for section, mapping in settings.SECTIONS.items():
        if key in mapping:
            return getattr(getattr(config, section), mapping[key])
    raise AssertionError(f"правка {key} не описана в SECTIONS")


def other_than_default(config, key: str):
    """Значение, заведомо отличное от того, что стоит в конфиге."""
    current = value_of(config, key)
    if isinstance(current, bool):
        return not current
    assert key in OTHER_VALUE, f"для правки {key} не задано значение, отличное от умолчания"
    assert OTHER_VALUE[key] != current, f"вариант для {key} совпал с умолчанием"
    return OTHER_VALUE[key]


@pytest.mark.parametrize("key", ALL_OVERRIDES)
def test_every_override_reaches_the_stages(paths, key) -> None:
    """Каждая правка доходит до конфига, с которым работает стадия.

    Пять переключателей — сигналы отбора, теги звука, модель, детектор лица и
    кодировщик — сохранялись, показывались сохранёнными и не влияли ни на что:
    файл настроек читали три разных места, и каждое знало свой набор ключей.
    """
    wanted = other_than_default(context(paths).config, key)

    settings.update(paths, {key: wanted})

    assert value_of(context(paths).config, key) == wanted


@pytest.mark.parametrize("key", ALL_OVERRIDES)
def test_every_override_changes_the_cache_key(paths, key) -> None:
    """И пересобирает то, что от неё зависит: настройка без пересборки — та же
    настройка, которая не сработала, только заметно это лишь на готовом ролике."""
    from narezka.stages import PIPELINE  # noqa: PLC0415

    before = {stage.name: stage.config_slice(context(paths)) for stage in PIPELINE}
    settings.update(paths, {key: other_than_default(context(paths).config, key)})
    after = {stage.name: stage.config_slice(context(paths)) for stage in PIPELINE}

    assert before != after, f"правка {key} не меняет ключ кэша ни одной стадии"


def test_ui_sends_nothing_that_settings_ignore() -> None:
    """Всё, что шлёт интерфейс, либо кадрирование, либо описанная правка.

    Поле, о котором не знает `SECTIONS`, — это ровно тот случай: на экране
    настройка есть, сохраняется, а до работы не доходит.
    """
    from narezka.api.app import FramingPayload  # noqa: PLC0415

    known = set(FramingConfig.model_fields) | set(ALL_OVERRIDES)
    unknown = set(FramingPayload.model_fields) - known

    assert not unknown, f"интерфейс шлёт правки, которых никто не применяет: {sorted(unknown)}"


def test_framing_keeps_the_settings_of_other_tabs(paths) -> None:
    """Сохранение кадрирования не трогает субтитры и длинную нарезку.

    Настройки всех вкладок лежат в одном файле. Пока кадрирование писало его
    целиком, выбор шрифта субтитров и собранные эпизоды пропадали молча — и
    узнать об этом можно было только по готовому ролику.
    """
    settings.update(paths, {settings.SUBTITLES_KEY: {"preset": "loud"}})
    settings.update(paths, {settings.COMPILATION_KEY: {"target_minutes": 25}})

    settings.update(paths, FramingConfig(preset="focus").model_dump())

    stored = settings.load(paths)
    assert stored[settings.SUBTITLES_KEY] == {"preset": "loud"}
    assert stored[settings.COMPILATION_KEY] == {"target_minutes": 25}
    assert stored["preset"] == "focus"


def test_reset_takes_only_what_it_was_asked(paths) -> None:
    """Сброс кадрирования оставляет чужие настройки на месте."""
    settings.update(paths, {settings.SUBTITLES_KEY: {"preset": "loud"}, "preset": "focus"})

    settings.reset(paths, set(FramingConfig.model_fields))

    assert settings.load(paths) == {settings.SUBTITLES_KEY: {"preset": "loud"}}


def test_reset_removes_the_file_when_nothing_is_left(paths) -> None:
    """А когда правок не осталось — убирает файл, а не пустой словарь в нём."""
    settings.update(paths, {"preset": "focus"})

    settings.reset(paths, {"preset"})

    assert not paths.framing.exists()


def test_broken_value_is_refused_at_saving(paths) -> None:
    """Негодное значение не должно молча откатываться к умолчанию.

    Иначе настройка выглядит применённой и врёт: на экране «yolox», в работе —
    прежний детектор.
    """
    config = load_config()

    with pytest.raises(ValueError):
        settings.apply(config, {"detector_backend": "ерунда"})
