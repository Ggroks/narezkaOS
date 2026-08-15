"""Тесты HTTP-слоя (BAZA.md §33).

Слой тонкий, но именно через него идёт вся ручная работа, и ошибка здесь
видна не в логе, а в виде пустого экрана. Проверяются коды ответов, поведение
на отсутствующих артефактах и то, что правки действительно сохраняются.

Хранилище подменяется временным: тесты не должны видеть настоящие видео
и тем более их менять.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from narezka.api import app as api_app
from narezka.core.config import load_config

VIDEO = "testvideo001"


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """Клиент API поверх пустого временного хранилища."""
    config = load_config()
    config = config.model_copy(update={"storage_root": tmp_path})

    from narezka.core.device import DeviceInfo

    device = DeviceInfo(kind="cpu", name="test", detail="тестовое окружение")
    monkeypatch.setattr(api_app, "_config", lambda profile=None: (config, device))
    return TestClient(api_app.app)


@pytest.fixture
def video(client, tmp_path: Path):
    """Зарегистрированное видео с минимальными артефактами."""
    base = tmp_path / "projects" / "default" / "videos" / VIDEO
    for name in ("analysis", "meta", "transcript", "shorts", "source", "subtitles"):
        (base / name).mkdir(parents=True, exist_ok=True)
    (base / "meta" / "metadata.json").write_text(
        json.dumps({"video_id": VIDEO, "has_video": True, "video": {"width": 1280, "height": 720}}),
        encoding="utf-8",
    )
    return base


def write(base: Path, relative: str, payload: dict) -> None:
    (base / relative).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


CANDIDATES = {
    "candidates": [
        {"start": 10.0, "end": 40.0, "peak_at": 20.0, "provisional_score": 2.0,
         "signals": {"loudness_z": 2.4}, "text": "первый"},
        {"start": 100.0, "end": 130.0, "peak_at": 110.0, "provisional_score": 1.5,
         "signals": {"loudness_z": 1.2}, "text": "второй"},
    ]
}


# --- служебные эндпоинты ---------------------------------------------------


def test_health_reports_environment(client) -> None:
    body = client.get("/api/health").json()
    assert "checks" in body and body["device"]["kind"] == "cpu"


def test_stages_list_is_not_empty(client) -> None:
    stages = client.get("/api/stages").json()
    names = [s["name"] for s in stages]
    assert "render" in names and "llm_select" in names


def test_unknown_video_gives_404(client) -> None:
    assert client.get("/api/videos/нетакого").status_code == 404


def test_missing_artifacts_give_404_not_500(client, video) -> None:
    """Артефакта ещё нет — это нормальный ход работы, а не поломка."""
    for path in ("transcript", "candidates", "shorts", "publish"):
        assert client.get(f"/api/videos/{VIDEO}/{path}").status_code == 404, path


# --- добавление видео ------------------------------------------------------


def test_add_requires_exactly_one_source(client) -> None:
    assert client.post("/api/videos", json={}).status_code == 400
    assert client.post("/api/videos", json={"url": "u", "file": "f"}).status_code == 400


def test_add_by_url_is_idempotent(client) -> None:
    """Тот же URL даёт тот же идентификатор — иначе повторное добавление
    создаёт дубль и работа расходится по двум каталогам."""
    first = client.post("/api/videos", json={"url": "https://twitch.tv/videos/1"}).json()
    second = client.post("/api/videos", json={"url": "https://twitch.tv/videos/1"}).json()
    assert first["video_id"] == second["video_id"]


def test_add_by_missing_file_is_rejected(client) -> None:
    assert client.post("/api/videos", json={"file": "/нет/такого.mp4"}).status_code == 400


# --- обзор и разметка ------------------------------------------------------


def test_review_merges_candidates_with_decisions(client, video) -> None:
    write(video, "analysis/candidates.json", CANDIDATES)
    body = client.get(f"/api/videos/{VIDEO}/review").json()
    assert body["stats"]["total"] == 2
    assert body["clips"][0]["verdict"] is None


def test_verdict_is_saved_and_visible(client, video) -> None:
    write(video, "analysis/candidates.json", CANDIDATES)
    body = client.put(f"/api/videos/{VIDEO}/review/0", json={"verdict": "accept"}).json()
    assert body["stats"]["accepted"] == 1
    # И переживает перезагрузку страницы — решения живут на сервере.
    assert client.get(f"/api/videos/{VIDEO}/review").json()["clips"][0]["verdict"] == "accept"


def test_review_rejects_unknown_clip(client, video) -> None:
    write(video, "analysis/candidates.json", CANDIDATES)
    assert client.put(f"/api/videos/{VIDEO}/review/99", json={"verdict": "accept"}).status_code == 404


def test_review_rejects_backwards_bounds(client, video) -> None:
    write(video, "analysis/candidates.json", CANDIDATES)
    response = client.put(f"/api/videos/{VIDEO}/review/0", json={"start": 50, "end": 20})
    assert response.status_code == 422


def test_review_shows_model_scores_when_selection_exists(client, video) -> None:
    write(video, "analysis/candidates.json", CANDIDATES)
    write(video, "analysis/selection.json", {"clips": [
        {"index": 1, "start": 105.0, "end": 128.0, "interest_score": 0.8, "rank": 1,
         "clip_type": "hook", "explanation": "смешно", "factors": {"semantic": 0.9}},
    ]})
    clips = client.get(f"/api/videos/{VIDEO}/review").json()["clips"]
    assert clips[1]["interest_score"] == 0.8
    assert clips[1]["selected"] is True
    # Границы показываются уточнённые моделью.
    assert clips[1]["start"] == 105.0
    # А нетронутый кандидат остаётся видимым: несогласие с отбором — сигнал.
    assert clips[0]["selected"] is False


def test_human_edit_wins_over_model_bounds(client, video) -> None:
    """Правка человека важнее уточнения модели — иначе его работа пропадает."""
    write(video, "analysis/candidates.json", CANDIDATES)
    write(video, "analysis/selection.json", {"clips": [
        {"index": 0, "start": 12.0, "end": 38.0, "interest_score": 0.5},
    ]})
    client.put(f"/api/videos/{VIDEO}/review/0", json={"start": 15.0})
    assert client.get(f"/api/videos/{VIDEO}/review").json()["clips"][0]["start"] == 15.0


# --- настройки ролика ------------------------------------------------------


def test_framing_defaults_are_returned(client, video) -> None:
    body = client.get(f"/api/videos/{VIDEO}/framing").json()
    assert body["custom"] is False
    assert body["source"] == {"width": 1280, "height": 720}
    assert len(body["presets"]) == 4


def test_framing_is_saved(client, video) -> None:
    payload = {"preset": "focus", "layout": "single", "subtitles_enabled": False,
               "loudnorm_enabled": True, "side_crop": 0.25, "anchor": "center",
               "background": "blur", "blur_sigma": 12.0, "color": "0x14171c"}
    body = client.put(f"/api/videos/{VIDEO}/framing", json=payload).json()
    assert body["current"]["preset"] == "focus"
    assert body["current"]["subtitles_enabled"] is False
    assert body["custom"] is True


def test_framing_rejects_out_of_range(client, video) -> None:
    assert client.put(f"/api/videos/{VIDEO}/framing", json={"side_crop": 5}).status_code == 422
    assert client.put(f"/api/videos/{VIDEO}/framing", json={"preset": "выдумка"}).status_code == 422


def test_framing_reset_returns_to_config(client, video) -> None:
    client.put(f"/api/videos/{VIDEO}/framing", json={"preset": "focus"})
    body = client.delete(f"/api/videos/{VIDEO}/framing").json()
    assert body["custom"] is False


def test_split_is_offered_only_with_a_webcam(client, video) -> None:
    """Предлагать раскладку, для которой нет данных, значит обещать несбыточное."""
    assert client.get(f"/api/videos/{VIDEO}/framing").json()["split_available"] is False
    write(video, "analysis/facecam.json", {"clips": {"0": {"full_frame": False, "x": 0, "y": 0,
                                                            "width": 280, "height": 210}}})
    assert client.get(f"/api/videos/{VIDEO}/framing").json()["split_available"] is True


def test_full_frame_camera_does_not_enable_split(client, video) -> None:
    write(video, "analysis/facecam.json", {"clips": {"0": {"full_frame": True, "x": 0, "y": 0,
                                                            "width": 1280, "height": 720}}})
    assert client.get(f"/api/videos/{VIDEO}/framing").json()["split_available"] is False


# --- результаты публикаций -------------------------------------------------


def test_publish_needs_a_selection(client, video) -> None:
    write(video, "analysis/candidates.json", CANDIDATES)
    response = client.post(f"/api/videos/{VIDEO}/performance/publish", json={"index": 0})
    assert response.status_code == 409


def test_metrics_need_a_frozen_clip(client, video) -> None:
    """Метрики без снимка признаков ничему не учат — записывать их некуда."""
    response = client.post(
        f"/api/videos/{VIDEO}/performance/metrics",
        json={"clip_id": "нет-такого", "views": 100},
    )
    assert response.status_code == 404


def test_metrics_reject_impossible_values(client, video) -> None:
    response = client.post(
        f"/api/videos/{VIDEO}/performance/metrics",
        json={"clip_id": "x", "retention": 3.0},
    )
    assert response.status_code == 422
