"""Тесты хранилища результатов (BAZA.md §31, §63).

Проверяется главное свойство: данные должны остаться пригодными для обучения
через месяцы. Признаки заморожены, версии записаны, история замеров цела.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from narezka.core import db


@pytest.fixture
def storage(tmp_path: Path) -> Path:
    return tmp_path


def clip(index: int = 0, score: float = 0.8, **extra) -> dict:
    base = {
        "clip_id": f"vid_c{index:02d}",
        "index": index,
        "start": 10.0,
        "end": 40.0,
        "interest_score": score,
        "factors": {"semantic": 0.9, "emotion": 0.7, "visual": None},
        "penalties": {"unresolved_ending": 0.1, "music_present": None},
        "score_schema_version": 2,
        "prompt_version": 2,
        "model": "nemotron",
    }
    base.update(extra)
    return base


def prepare(connection, **kwargs) -> str:
    db.upsert_video(connection, video_id="vid", project_id="default")
    return db.freeze_clip(connection, clip=kwargs.pop("clip", clip()), video_id="vid", **kwargs)


# --- заморозка вектора -----------------------------------------------------


def test_snapshot_survives_recalculation(storage: Path) -> None:
    """§63: главное свойство всей таблицы.

    Стадии пересчитываются, промпт правится, веса меняются. Если снимок
    не заморозить, через месяц будет непонятно, какие признаки сработали,
    и цифры просмотров ничему не научат.
    """
    with db.connect(storage) as connection:
        prepare(connection, weights={"semantic": 0.25}, platform="youtube")
        # Клип пересчитался, числа поехали.
        db.freeze_clip(
            connection,
            clip=clip(score=0.1, factors={"semantic": 0.1, "emotion": 0.1}),
            video_id="vid",
            platform="youtube",
        )
        row = db.clip_rows(connection)[0]

    assert row["factors_snapshot"]["semantic"] == 0.9
    assert row["interest_score"] == 0.8


def test_versions_are_stored_with_the_snapshot(storage: Path) -> None:
    """Без версий данные разных версий системы смешиваются в одну кучу."""
    with db.connect(storage) as connection:
        prepare(connection, weights={"semantic": 0.25, "emotion": 0.2})
        row = db.clip_rows(connection)[0]

    assert row["score_schema_version"] == 2
    assert row["prompt_version"] == 2
    assert row["model"] == "nemotron"
    assert row["weights_snapshot"]["semantic"] == 0.25


def test_unmeasured_factors_stay_null_in_storage(storage: Path) -> None:
    """None — «не измеряли». Превратить его в ноль значит выдать пробел
    за результат (§54)."""
    with db.connect(storage) as connection:
        prepare(connection)
        row = db.clip_rows(connection)[0]

    assert row["factors_snapshot"]["visual"] is None
    assert row["penalties_snapshot"]["music_present"] is None


def test_publication_details_can_be_updated(storage: Path) -> None:
    """Ссылку добавляют позже — это не повод переписывать снимок."""
    with db.connect(storage) as connection:
        prepare(connection, platform="youtube")
        db.freeze_clip(
            connection, clip=clip(), video_id="vid", platform="youtube",
            url="https://example.com/v",
        )
        row = db.clip_rows(connection)[0]

    assert row["url"] == "https://example.com/v"
    assert row["factors_snapshot"]["semantic"] == 0.9


def test_human_signals_are_stored(storage: Path) -> None:
    """Решение человека и его поправка границ — тоже обучающий сигнал."""
    with db.connect(storage) as connection:
        prepare(connection, human_verdict="accept", bounds_shift=(3.0, -1.5))
        row = db.clip_rows(connection)[0]

    assert row["human_verdict"] == "accept"
    assert row["bounds_shift_start"] == 3.0
    assert row["bounds_shift_end"] == -1.5


# --- замеры ----------------------------------------------------------------


def test_measurements_accumulate(storage: Path) -> None:
    """История важнее последнего значения: рост за неделю говорит больше,
    чем итоговое число."""
    with db.connect(storage) as connection:
        clip_id = prepare(connection, platform="youtube")
        db.add_measurement(connection, clip_id=clip_id, platform="youtube",
                           measured_at="2026-08-01T00:00:00+00:00", views=100)
        db.add_measurement(connection, clip_id=clip_id, platform="youtube",
                           measured_at="2026-08-08T00:00:00+00:00", views=5400)
        history = db.measurements(connection, clip_id)

    assert [m["views"] for m in history] == [100, 5400]


def test_latest_measurement_is_the_one_reported(storage: Path) -> None:
    with db.connect(storage) as connection:
        clip_id = prepare(connection, platform="youtube")
        db.add_measurement(connection, clip_id=clip_id, platform="youtube",
                           measured_at="2026-08-01T00:00:00+00:00", views=100)
        db.add_measurement(connection, clip_id=clip_id, platform="youtube",
                           measured_at="2026-08-08T00:00:00+00:00", views=5400)
        row = db.clip_rows(connection)[0]

    assert row["views"] == 5400


def test_measurement_needs_a_frozen_clip(storage: Path) -> None:
    """Метрики без снимка признаков бесполезны — записывать их некуда."""
    with db.connect(storage) as connection:
        with pytest.raises(ValueError, match="не зафиксирован"):
            db.add_measurement(connection, clip_id="нет-такого", platform="youtube", views=10)


def test_measured_at_is_filled_automatically(storage: Path) -> None:
    """Дата обязательна: тысяча просмотров за сутки и за месяц — разное."""
    with db.connect(storage) as connection:
        clip_id = prepare(connection, platform="youtube")
        db.add_measurement(connection, clip_id=clip_id, platform="youtube", views=10)
        assert db.measurements(connection, clip_id)[0]["measured_at"]


# --- схема -----------------------------------------------------------------


def test_schema_is_created_on_first_open(storage: Path) -> None:
    with db.connect(storage) as connection:
        assert connection.execute("SELECT version FROM schema_meta").fetchone()["version"] == 1
    assert db.db_path(storage).is_file()


def test_reopening_does_not_duplicate_anything(storage: Path) -> None:
    with db.connect(storage) as connection:
        prepare(connection)
    with db.connect(storage) as connection:
        assert len(db.clip_rows(connection)) == 1
        assert connection.execute("SELECT COUNT(*) c FROM schema_meta").fetchone()["c"] == 1


def test_video_fields_from_spec_are_present(storage: Path) -> None:
    """§31: content_origin и retention_until закладываются сразу, чтобы
    потом это была не миграция."""
    with db.connect(storage) as connection:
        db.upsert_video(
            connection, video_id="vid", project_id="default",
            content_origin="third_party", retention_until="2026-12-31",
        )
        row = connection.execute("SELECT * FROM videos").fetchone()

    assert row["content_origin"] == "third_party"
    assert row["retention_until"] == "2026-12-31"


def test_clips_are_filtered_by_video(storage: Path) -> None:
    with db.connect(storage) as connection:
        db.upsert_video(connection, video_id="a", project_id="p")
        db.upsert_video(connection, video_id="b", project_id="p")
        db.freeze_clip(connection, clip=clip(0), video_id="a")
        db.freeze_clip(connection, clip=clip(1), video_id="b")

        assert len(db.clip_rows(connection, "a")) == 1
        assert len(db.clip_rows(connection)) == 2
