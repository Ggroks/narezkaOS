"""Тесты освобождения места (BAZA.md §65).

Главное, что здесь проверяется, — безопасность: удалять можно только то,
что восстановимо или больше не нужно. Ошибка в эту сторону необратима.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from narezka.core import retention
from narezka.core.paths import video_paths


@pytest.fixture
def paths(tmp_path: Path):
    p = video_paths(tmp_path, "default", "vid")
    p.ensure()
    return p


def mark_done(paths, *names: str) -> None:
    for name in names:
        (paths.stage_state / f"{name}.json").write_text("{}", encoding="utf-8")


def set_origin(paths, kind: str) -> None:
    (paths.metadata).write_text(
        json.dumps({"origin": {"type": kind, "url": "https://twitch.tv/videos/1"}}),
        encoding="utf-8",
    )


def put(directory: Path, name: str, size: int = 4096) -> Path:
    path = directory / name
    path.write_bytes(b"x" * size)
    return path


# --- когда удалять рано ----------------------------------------------------


def test_source_is_kept_until_render_is_done(paths) -> None:
    """Пока стадии не отработали, удаление исходника остановит работу."""
    put(paths.source, "source.mp4")
    set_origin(paths, "url")
    mark_done(paths, "probe", "extract_audio")

    assert [c.kind for c in retention.inspect(paths, "vid")] == []


def test_source_is_offered_after_render(paths) -> None:
    put(paths.source, "source.mp4")
    set_origin(paths, "url")
    mark_done(paths, "probe", "extract_audio", "render")

    found = {c.kind: c for c in retention.inspect(paths, "vid")}
    assert "source" in found
    assert found["source"].recoverable == "скачиванием"


def test_local_file_source_is_marked_unrecoverable_by_us(paths) -> None:
    """Локальный файл лежит у пользователя: восстановить его мы не сможем,
    и это должно быть написано прямо."""
    put(paths.source, "source.mp4")
    set_origin(paths, "local_file")
    mark_done(paths, "probe", "extract_audio", "render")

    source = next(c for c in retention.inspect(paths, "vid") if c.kind == "source")
    assert "пользовател" in source.recoverable


def test_symlinked_source_counts_as_no_space(paths) -> None:
    """Ссылка на чужой файл места не занимает. Считать её размером значит
    обещать освободить чужие гигабайты."""
    real = paths.base.parent / "внешний.mp4"
    real.write_bytes(b"x" * 100_000)
    (paths.source / "source.mp4").symlink_to(real)
    set_origin(paths, "local_file")
    mark_done(paths, "probe", "extract_audio", "render")

    assert not [c for c in retention.inspect(paths, "vid") if c.kind == "source"]


# --- что предлагается вместе с исходником ----------------------------------


def test_audio_is_offered_after_render(paths) -> None:
    put(paths.audio, "audio.wav", 10_000)
    mark_done(paths, "render")

    audio = next(c for c in retention.inspect(paths, "vid") if c.kind == "audio")
    assert audio.size_bytes == 10_000


def test_audio_is_kept_before_render(paths) -> None:
    put(paths.audio, "audio.wav")
    assert not [c for c in retention.inspect(paths, "vid") if c.kind == "audio"]


def test_intermediate_clips_are_offered(paths) -> None:
    """§65: промежуточные нарезки удаляются после сборки финального ролика."""
    put(paths.clips, "00.mp4", 5_000)
    mark_done(paths, "render")
    assert [c.kind for c in retention.inspect(paths, "vid") if c.kind == "clips"] == ["clips"]


def test_nothing_to_free_on_empty_video(paths) -> None:
    assert retention.inspect(paths, "vid") == []


# --- удаление --------------------------------------------------------------


def test_free_removes_content_but_keeps_the_directory(paths) -> None:
    """Каталог нужен: раскладка хранилища создаётся один раз (§32),
    и стадии рассчитывают, что он на месте."""
    put(paths.audio, "audio.wav", 8_000)
    mark_done(paths, "render")

    candidate = next(c for c in retention.inspect(paths, "vid") if c.kind == "audio")
    assert retention.free(candidate) == 8_000
    assert paths.audio.is_dir()
    assert not list(paths.audio.iterdir())


def test_summary_groups_by_kind(paths) -> None:
    put(paths.audio, "audio.wav", 2_000)
    put(paths.clips, "00.mp4", 3_000)
    mark_done(paths, "render")

    summary = retention.summarize(retention.inspect(paths, "vid"))
    assert summary["count"] == 2
    assert summary["bytes"] == 5_000
    assert set(summary["by_kind"]) == {"audio", "clips"}


# --- уборка на живом сервере -----------------------------------------------


def sweepable(tmp_path: Path, days_old: float = 30.0):
    """Запись, которую уборка вправе почистить: всё сделано, исходник отлежался."""
    import os
    import time

    paths = video_paths(tmp_path, "default", "vid")
    paths.ensure()
    put(paths.source, "source.mp4")
    set_origin(paths, "url")
    mark_done(paths, "probe", "extract_audio", "render")

    old = time.time() - days_old * 86400
    for item in paths.source.rglob("*"):
        os.utime(item, (old, old))
    return paths


def config_with(days: int):
    from narezka.core.config import load_config

    config = load_config()
    return config.model_copy(
        update={"retention": config.retention.model_copy(update={"source_days": days})}
    )


def test_sweep_frees_what_has_lain_long_enough(tmp_path: Path) -> None:
    paths = sweepable(tmp_path)

    report = retention.sweep(tmp_path, config_with(14), apply=True)

    assert [item["kind"] for item in report["items"]] == ["source"]
    assert not list(paths.source.iterdir())


def test_sweep_spares_a_record_that_is_being_worked_on(tmp_path: Path) -> None:
    """Человек открыл старый проект и нажал «собрать».

    Уборщик просыпается раз в несколько часов и не знает, что происходит
    прямо сейчас. Убрать исходник из-под идущей работы значит уронить её
    на середине — и списать за это кредиты.
    """
    from narezka.core import db, queue

    paths = sweepable(tmp_path)
    with db.connect(tmp_path) as connection:
        queue.enqueue(connection, workspace="default", video_id="vid", work_group="shorts")

    report = retention.sweep(tmp_path, config_with(14), apply=True)

    assert report["items"] == []
    assert (paths.source / "source.mp4").exists(), "исходник удалён во время работы"


def test_sweep_does_nothing_when_the_queue_is_unreadable(tmp_path: Path, monkeypatch) -> None:
    """Не знаем, что в работе, — не трогаем ничего и говорим об этом.

    Потерять исходник хуже, чем не освободить место.
    """
    paths = sweepable(tmp_path)
    monkeypatch.setattr(retention, "_busy", lambda root: None)
    report = retention.sweep(tmp_path, config_with(14), apply=True)

    assert report["items"] == [] and "skipped" in report
    assert (paths.source / "source.mp4").exists()
