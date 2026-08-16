"""Стадия timeline: связывает ось времени с пайплайном."""

import json
from pathlib import Path

import pytest

from narezka.core.config import load_config
from narezka.core.device import DeviceInfo
from narezka.core.edl import Edl
from narezka.core.logging import get_logger
from narezka.core.paths import video_paths
from narezka.core.stage import StageContext
from narezka.stages.timeline import TIMELINE_NAME, TimelineStage


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    config = load_config().model_copy(update={"storage_root": tmp_path})
    paths = video_paths(tmp_path, "default", "тест")
    paths.ensure()
    return StageContext(
        project_id="default", video_id="тест", paths=paths, config=config,
        device=DeviceInfo(kind="cpu", name="test"), log=get_logger("timeline"),
    )


def test_disabled_writes_identity(ctx):
    """Выключено — не пропуск стадии, а осознанный результат.

    Тождественная ось означает «правок нет», и потребители пользуются ей
    так же, как настоящей. Пропустив стадию, мы заставили бы каждого из них
    проверять наличие артефакта.
    """
    ctx.config.timeline.remove_silence = False

    TimelineStage().run(ctx)

    data = json.loads((ctx.paths.analysis / TIMELINE_NAME).read_text())
    assert data["enabled"] is False
    assert Edl.from_dict(data["edl"]).is_identity, "ось тождественна"
    assert data["removed_seconds"] == 0.0


def test_config_slice_covers_every_knob():
    """Все настройки входят в ключ кэша.

    Настройка, не попавшая в срез, меняла бы результат молча: стадия
    отдавала бы прежний ответ из кэша после правки порога.
    """
    stage = TimelineStage()
    from narezka.core.config import TimelineConfig

    knobs = set(TimelineConfig.model_fields)

    class FakeCtx:
        class config:
            timeline = TimelineConfig()

    covered = set(stage.config_slice(FakeCtx()))
    missing = {k for k in knobs if k not in covered and f"{k}" not in covered}
    assert not missing, f"настройки вне ключа кэша: {missing}"


def test_stage_runs_before_candidates():
    """Ось времени раньше отбора: иначе границы кандидатов указывали бы
    на места, которых в ролике нет."""
    from narezka.stages import PIPELINE

    order = [s.name for s in PIPELINE]
    assert order.index("timeline") < order.index("candidates")
    assert order.index("transcribe") < order.index("timeline")
