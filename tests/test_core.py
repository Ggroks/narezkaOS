"""Тесты инвариантов этапа 0.

Проверяется ровно то, ради чего этап 0 стоит первым (BAZA.md §58):
атомарность записи, корректность кэша и его сброс при значимых изменениях.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from narezka.core import cache
from narezka.core.artifacts import Artifact, cleanup_partials
from narezka.core.config import _deep_merge, load_config, resolve_profile
from narezka.core.device import DeviceInfo
from narezka.core.media import parse_fps
from narezka.core.paths import video_paths
from narezka.core.runner import Outcome, run_stage
from narezka.core.stage import Device, Stage, StageContext


# --- артефакты: атомарность ------------------------------------------------


def test_atomic_write_leaves_no_partial_on_failure(tmp_path: Path) -> None:
    target = Artifact(tmp_path / "out.json")

    with pytest.raises(RuntimeError):
        with target.open_write() as handle:
            handle.write("наполовину записанные данные")
            raise RuntimeError("прерывание посреди записи")

    assert not target.exists(), "целевой файл не должен появиться при сбое"
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert leftovers == [], f"остался мусор: {leftovers}"


def test_atomic_write_does_not_clobber_previous_version(tmp_path: Path) -> None:
    target = Artifact(tmp_path / "out.txt")
    target.write_json({"версия": 1})

    with pytest.raises(RuntimeError):
        with target.open_write() as handle:
            handle.write("мусор")
            raise RuntimeError("сбой")

    assert target.read_json() == {"версия": 1}, "прежнее содержимое должно уцелеть"


def test_reserve_publishes_only_on_success(tmp_path: Path) -> None:
    target = Artifact(tmp_path / "video.mp4")

    with target.reserve() as tmp:
        tmp.write_bytes(b"data")
    assert target.exists()

    with pytest.raises(RuntimeError):
        with target.reserve() as tmp:
            tmp.write_bytes(b"broken")
            raise RuntimeError("сбой инструмента")
    assert target.path.read_bytes() == b"data"


def test_reserve_detects_tool_that_wrote_nothing(tmp_path: Path) -> None:
    target = Artifact(tmp_path / "video.mp4")
    with pytest.raises(FileNotFoundError):
        with target.reserve():
            pass


def test_cleanup_partials(tmp_path: Path) -> None:
    (tmp_path / ".a.partial").write_text("x")
    (tmp_path / ".b.tmp").write_text("x")
    (tmp_path / "keep.json").write_text("x")
    assert cleanup_partials(tmp_path) == 2
    assert (tmp_path / "keep.json").exists()


def test_fingerprint_changes_with_content(tmp_path: Path) -> None:
    artifact = Artifact(tmp_path / "f.txt")
    assert artifact.fingerprint() == "absent"
    artifact.write_json({"a": 1})
    first = artifact.fingerprint()
    artifact.write_json({"a": 2})
    assert artifact.fingerprint() != first


# --- стадия-заглушка для проверки кэша -------------------------------------


class CountingStage(Stage):
    name = "counting"
    version = 1
    device = Device.ANY

    def __init__(self) -> None:
        self.runs = 0
        self.slice: dict = {"setting": "a"}

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.source / "input.txt")]

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.analysis / "result.json")]

    def config_slice(self, ctx: StageContext) -> dict:
        return self.slice

    def run(self, ctx: StageContext) -> None:
        self.runs += 1
        self.outputs(ctx)[0].write_json({"runs": self.runs})


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    paths = video_paths(tmp_path / "storage", "default", "vid1")
    paths.ensure()
    (paths.source / "input.txt").write_text("исходные данные", encoding="utf-8")
    return StageContext(
        project_id="default",
        video_id="vid1",
        paths=paths,
        config=load_config(Path("configs/config.yaml")),
        device=DeviceInfo("cpu", "test"),
        log=logging.getLogger("test"),
    )


def test_second_run_is_cached(ctx: StageContext) -> None:
    stage = CountingStage()
    assert run_stage(stage, ctx).outcome is Outcome.DONE
    assert run_stage(stage, ctx).outcome is Outcome.CACHED
    assert stage.runs == 1


def test_force_ignores_cache(ctx: StageContext) -> None:
    stage = CountingStage()
    run_stage(stage, ctx)
    assert run_stage(stage, ctx, force=True).outcome is Outcome.DONE
    assert stage.runs == 2


def test_changed_input_invalidates_cache(ctx: StageContext) -> None:
    stage = CountingStage()
    run_stage(stage, ctx)
    (ctx.paths.source / "input.txt").write_text("другие данные", encoding="utf-8")
    assert run_stage(stage, ctx).outcome is Outcome.DONE
    assert stage.runs == 2


def test_changed_config_slice_invalidates_cache(ctx: StageContext) -> None:
    stage = CountingStage()
    run_stage(stage, ctx)
    stage.slice = {"setting": "b"}
    assert run_stage(stage, ctx).outcome is Outcome.DONE


def test_bumped_stage_version_invalidates_cache(ctx: StageContext) -> None:
    stage = CountingStage()
    run_stage(stage, ctx)
    type(stage).version = 2
    try:
        assert run_stage(stage, ctx).outcome is Outcome.DONE
    finally:
        type(stage).version = 1


def test_deleted_output_invalidates_cache(ctx: StageContext) -> None:
    """Совпадения ключа мало: выходы должны физически существовать."""
    stage = CountingStage()
    run_stage(stage, ctx)
    stage.outputs(ctx)[0].path.unlink()
    assert run_stage(stage, ctx).outcome is Outcome.DONE
    assert stage.runs == 2


def test_missing_input_fails_before_running(ctx: StageContext) -> None:
    stage = CountingStage()
    (ctx.paths.source / "input.txt").unlink()
    result = run_stage(stage, ctx)
    assert result.outcome is Outcome.FAILED
    assert stage.runs == 0
    assert "нет входных артефактов" in result.reason


def test_gpu_stage_skipped_on_cpu_when_optional(ctx: StageContext) -> None:
    class GpuStage(CountingStage):
        name = "gpu_only"
        device = Device.GPU
        optional = True

    result = run_stage(GpuStage(), ctx)
    assert result.outcome is Outcome.SKIPPED
    assert "ускоритель" in result.reason


def test_gpu_stage_fails_on_cpu_when_required(ctx: StageContext) -> None:
    class GpuStage(CountingStage):
        name = "gpu_required"
        device = Device.GPU
        optional = False

    assert run_stage(GpuStage(), ctx).outcome is Outcome.FAILED


def test_stage_that_writes_nothing_is_not_cached(ctx: StageContext) -> None:
    class EmptyStage(CountingStage):
        name = "empty"

        def run(self, ctx: StageContext) -> None:
            self.runs += 1  # выход не создаётся

    stage = EmptyStage()
    result = run_stage(stage, ctx)
    assert result.outcome is Outcome.FAILED
    assert cache.load_state(stage, ctx) is None


# --- конфигурация ----------------------------------------------------------


def test_deep_merge_is_recursive_and_pure() -> None:
    base = {"stt": {"model": "large-v3", "batch_size": 8}, "device": "auto"}
    overlay = {"stt": {"model": "base"}}
    merged = _deep_merge(base, overlay)
    assert merged == {"stt": {"model": "base", "batch_size": 8}, "device": "auto"}
    assert base["stt"]["model"] == "large-v3", "аргументы не должны меняться"


def test_dev_profile_lowers_model() -> None:
    """dev берёт модель меньше, чем batch, — конкретное имя решается замерами."""
    dev = load_config(Path("configs/config.yaml"), profile_override="dev")
    batch = load_config(Path("configs/config.yaml"), profile_override="batch")
    assert dev.resolved_profile == "dev"
    assert dev.stt.model != batch.stt.model
    assert not dev.stt.model.startswith("large")
    assert dev.stt.compute_type == "int8"


def test_batch_profile_keeps_large_model() -> None:
    config = load_config(Path("configs/config.yaml"), profile_override="batch")
    assert config.stt.model == "large-v3"


def test_auto_profile_follows_accelerator() -> None:
    assert resolve_profile("auto", has_accelerator=False) == "dev"
    assert resolve_profile("auto", has_accelerator=True) == "batch"
    assert resolve_profile("dev", has_accelerator=True) == "dev"


def test_missing_config_file_falls_back_to_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "нет-такого.yaml")
    assert config.stt.model == "large-v3"


# --- медиа -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("30/1", 30.0), ("60000/1001", 59.94), ("0/0", None), (None, None), ("N/A", None)],
)
def test_parse_fps(raw: str | None, expected: float | None) -> None:
    assert parse_fps(raw) == expected
