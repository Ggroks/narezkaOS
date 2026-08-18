"""Тесты HTTP-слоя (BAZA.md §33).

Слой тонкий, но именно через него идёт вся ручная работа, и ошибка здесь
видна не в логе, а в виде пустого экрана. Проверяются коды ответов, поведение
на отсутствующих артефактах и то, что правки действительно сохраняются.

Хранилище подменяется временным: тесты не должны видеть настоящие видео
и тем более их менять.
"""

from __future__ import annotations

import json
import time
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


# --- каталог проектов ------------------------------------------------------


def test_own_title_is_saved_and_shown(client) -> None:
    """Название, данное человеком, важнее заголовка из источника.

    В каталоге из десяти записей «Стрим #482 [VOD]» друг от друга
    не отличаются, а своё имя отличает.
    """
    added = client.post(
        "/api/videos", json={"url": "https://twitch.tv/videos/7", "title": "Разбор катки"}
    ).json()
    card = next(v for v in client.get("/api/videos").json() if v["video_id"] == added["video_id"])
    assert card["title"] == "Разбор катки" and card["named"] is True


def test_rename_and_reset_to_source_title(client, video) -> None:
    write(video, "meta/metadata.json", {"video_id": VIDEO, "source_title": "VOD 4821"})

    renamed = client.patch(f"/api/videos/{VIDEO}", json={"title": "Про котов"}).json()
    assert renamed["title"] == "Про котов" and renamed["source_title"] == "VOD 4821"

    # Пустое название — не ошибка, а возврат к заголовку площадки.
    restored = client.patch(f"/api/videos/{VIDEO}", json={"title": ""}).json()
    assert restored["title"] == "VOD 4821" and restored["named"] is False


def test_rename_of_unknown_video_is_404(client) -> None:
    assert client.patch("/api/videos/нетакого", json={"title": "х"}).status_code == 404


def test_too_long_title_is_refused(client, video) -> None:
    assert client.patch(f"/api/videos/{VIDEO}", json={"title": "я" * 500}).status_code == 422


def test_catalog_state_follows_the_work(client, video) -> None:
    """Черновик → в работе → готов. Три разных ответа, а не два."""
    def state() -> str:
        return client.get("/api/videos").json()[0]["state"]

    assert state() == "draft"

    (video / "meta" / "stages").mkdir(parents=True, exist_ok=True)
    write(video, "meta/stages/probe.json", {"finished_at": "2026-08-17T10:00:00"})
    card = client.get("/api/videos").json()[0]
    assert card["state"] == "started" and card["stages_done"] == 1

    write(video, "shorts/index.json", {"files": [{"index": 0}, {"index": 1}]})
    card = client.get("/api/videos").json()[0]
    assert card["state"] == "ready" and card["shorts"] == 2


def test_catalog_says_when_there_is_no_frame(client, video) -> None:
    """poster_at решает сервер: иначе карточка просит кадр и получает ошибку.

    Запись не скачана — кадра нет, и об этом надо сказать до запроса,
    а не показать битую картинку.
    """
    assert client.get("/api/videos").json()[0]["poster_at"] is None

    write(video, "meta/metadata.json", {"video_id": VIDEO, "has_video": False})
    (video / "source" / "audio.mp3").write_bytes(b"\x00")
    assert client.get("/api/videos").json()[0]["poster_at"] is None


def test_added_date_is_reported_even_for_old_videos(client, video) -> None:
    """У записей, заведённых до появления поля, дата берётся по каталогу."""
    assert client.get("/api/videos").json()[0]["created_at"]


# --- запуск по частям ------------------------------------------------------


def test_run_puts_the_group_in_the_queue(client, video) -> None:
    """Кнопка на вкладке ставит в очередь свой кусок работы, а не весь
    пайплайн, и не запускает его прямо из запроса."""
    from narezka.core import queue

    body = client.post(f"/api/videos/{VIDEO}/run", json={"group": "shorts"}).json()
    assert body["queued"] is True and body["position"] == 1

    config, _ = api_app._config()
    with api_app.db.connect(config.storage_root) as connection:
        task = queue.active_for(connection, workspace="default", video_id=VIDEO)
    assert task is not None and task.work_group == "shorts"


def test_second_press_does_not_add_a_second_task(client, video) -> None:
    """Двойной клик по «Собрать» не должен ставить две сборки подряд."""
    first = client.post(f"/api/videos/{VIDEO}/run", json={"group": "shorts"}).json()
    second = client.post(f"/api/videos/{VIDEO}/run", json={"group": "shorts"}).json()
    assert first["started"] is True and second["started"] is False


def test_queued_record_says_it_is_waiting(client, video) -> None:
    """«Идёт обработка» на записи, до которой очередь не дошла, — неправда."""
    client.post(f"/api/videos/{VIDEO}/run", json={"group": "analysis"})
    card = client.get("/api/videos").json()[0]
    assert card["state"] == "queued" and card["queue_position"] == 1


def test_stop_removes_a_task_from_the_queue(client, video) -> None:
    client.post(f"/api/videos/{VIDEO}/run", json={"group": "analysis"})
    assert client.post(f"/api/videos/{VIDEO}/stop").json()["from_queue"] is True
    assert client.get("/api/videos").json()[0]["state"] != "queued"
    # Снимать нечего — честный отказ, а не молчаливое согласие.
    assert client.post(f"/api/videos/{VIDEO}/stop").status_code == 409


def test_unknown_group_is_a_clear_400(client, video) -> None:
    response = client.post(f"/api/videos/{VIDEO}/run", json={"group": "чтонибудь"})
    assert response.status_code == 400
    assert "кусок работы" in response.json()["detail"]


def test_groups_are_published_for_the_interface(client) -> None:
    body = client.get("/api/groups").json()["groups"]
    assert {g["name"] for g in body} == {"analysis", "shorts", "long"}
    assert all(g["stages"] for g in body)


# --- длинная нарезка -------------------------------------------------------


def test_long_cut_choice_is_saved_per_video(client, video) -> None:
    """Выбор человека переживает перезагрузку страницы: без сохранения
    галочки на вкладке были бы украшением, а кнопка собирала бы по умолчанию."""
    saved = client.put(
        f"/api/videos/{VIDEO}/compilation",
        json={"story": True, "best": False, "episodes": [0, 2], "target_minutes": 15},
    ).json()
    assert saved["story"] is True and saved["selected"] == [0, 2]
    assert saved["target_minutes"] == 15

    again = client.get(f"/api/videos/{VIDEO}/episodes").json()
    assert again["story"] is True and again["selected"] == [0, 2]


def test_choosing_nothing_turns_the_stage_off(client, video) -> None:
    """Ни сюжета, ни подборки — собирать нечего, и стадия это знает."""
    from narezka.core import settings as core_settings
    from narezka.core.paths import video_paths

    client.put(
        f"/api/videos/{VIDEO}/compilation",
        json={"story": False, "best": False, "target_minutes": 20},
    )
    config, _ = api_app._config()
    effective = core_settings.compilation(
        config, video_paths(config.storage_root, "default", VIDEO)
    )
    assert effective.enabled is False and effective.find_episodes is False


def test_episode_settings_are_available_before_the_search(client, video) -> None:
    """Вкладка настраивается до того, как эпизоды посчитаны, — иначе выбрать
    режим нельзя, а без выбора нечего и считать."""
    body = client.get(f"/api/videos/{VIDEO}/episodes").json()
    assert body["episodes"] == [] and "reason" in body
    assert "story" in body and "target_minutes" in body


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


def test_framing_keeps_subtitles_and_compilation(client, video) -> None:
    """Настройки всех вкладок лежат в одном файле, и вкладка правит только своё.

    Кадрирование писало файл целиком: человек менял рамку — и терял шрифт
    субтитров вместе с выбранными эпизодами, ничего об этом не узнав.
    """
    client.put(f"/api/videos/{VIDEO}/subtitles", json={"preset": "loud", "font": "Oswald"})
    client.put(f"/api/videos/{VIDEO}/compilation", json={"best": True, "target_minutes": 25})

    client.put(f"/api/videos/{VIDEO}/framing", json={"preset": "focus"})

    subtitles = client.get(f"/api/videos/{VIDEO}/subtitles").json()
    assert subtitles["style"]["font"] == "Oswald"
    assert subtitles["preset"] == "loud"
    assert client.get(f"/api/videos/{VIDEO}/episodes").json()["target_minutes"] == 25

    # И сброс кадрирования — тоже только своё.
    client.delete(f"/api/videos/{VIDEO}/framing")
    assert client.get(f"/api/videos/{VIDEO}/subtitles").json()["style"]["font"] == "Oswald"


def test_per_video_model_reaches_the_stage(client, video) -> None:
    """Правка, сохранённая по API, доходит до конфига, с которым идёт работа."""
    client.put(f"/api/videos/{VIDEO}/framing", json={"llm_model": "другой/поставщик"})

    from narezka.api.app import _context  # noqa: PLC0415

    assert _context(VIDEO, "default").config.llm.model == "другой/поставщик"
    assert client.get(f"/api/videos/{VIDEO}/framing").json()["current"]["llm_model"] == (
        "другой/поставщик"
    )


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


# --- оформление субтитров --------------------------------------------------


def test_subtitle_presets_are_offered_with_names(client) -> None:
    """«STYLE_2» не говорит ничего, пока не увидишь: наборы названы тем,
    что видно в кадре."""
    body = client.get("/api/settings/subtitles").json()
    names = {p["name"] for p in body["presets"]}
    assert {"classic", "loud", "one_word"} <= names
    assert all(p["title"] and p["note"] for p in body["presets"])
    assert body["fonts"] and body["positions"] and body["face_zoom"]


def test_subtitle_settings_are_saved_for_the_record(client, video) -> None:
    saved = client.put(
        f"/api/videos/{VIDEO}/subtitles",
        json={"preset": "loud", "highlight": "#FF3B30", "max_words_per_line": 2},
    ).json()
    assert saved["preset"] == "loud" and saved["custom"] is True
    assert saved["style"]["highlight_hex"] == "#FF3B30"
    assert saved["style"]["max_words_per_line"] == 2
    # Остальное осталось от набора, а не сбросилось в умолчания.
    assert saved["style"]["font_size"] == 78

    again = client.get(f"/api/videos/{VIDEO}/subtitles").json()
    assert again["style"]["highlight_hex"] == "#FF3B30"


def test_subtitle_reset_returns_to_the_preset(client, video) -> None:
    client.put(
        f"/api/videos/{VIDEO}/subtitles", json={"preset": "loud", "highlight": "#FF3B30"}
    )
    back = client.delete(f"/api/videos/{VIDEO}/subtitles").json()
    assert back["custom"] is False


def test_unknown_preset_is_refused(client, video) -> None:
    assert client.put(
        f"/api/videos/{VIDEO}/subtitles", json={"preset": "красивые"}
    ).status_code == 422


def test_bad_colour_is_refused(client, video) -> None:
    assert client.put(
        f"/api/videos/{VIDEO}/subtitles", json={"preset": "classic", "primary": "жёлтый"}
    ).status_code == 422


def test_subtitle_settings_land_in_the_cache_key(client, video) -> None:
    """Правка обязана пересобрать субтитры: иначе ролик остаётся с прежними,
    и выглядит это как «настройка не работает»."""
    from narezka.core.paths import video_paths
    from narezka.stages.subtitles import SubtitlesStage
    from narezka.core.device import DeviceInfo
    from narezka.core.stage import StageContext
    from narezka.core.logging import get_logger

    config, _ = api_app._config()
    paths = video_paths(config.storage_root, "default", VIDEO)
    ctx = StageContext(
        project_id="default", video_id=VIDEO, paths=paths, config=config,
        device=DeviceInfo(kind="cpu", name="test"), log=get_logger("test"),
    )
    before = SubtitlesStage().config_slice(ctx)
    client.put(f"/api/videos/{VIDEO}/subtitles", json={"preset": "one_word"})
    assert SubtitlesStage().config_slice(ctx) != before


def test_single_short_can_be_rerendered(client, video) -> None:
    """Полная пересборка тридцати роликов — десятки минут, а поправить
    обычно надо один."""
    from narezka.core import queue

    write(video, "analysis/candidates.json", CANDIDATES)
    body = client.post(f"/api/videos/{VIDEO}/shorts/1/render").json()
    assert body["started"] is True

    config, _ = api_app._config()
    with api_app.db.connect(config.storage_root) as connection:
        task = queue.active_for(connection, workspace="default", video_id=VIDEO)
    assert task.stage == "render" and task.clip_index == 1


def test_rerender_goes_through_the_same_queue(client, video) -> None:
    """Две дороги к ffmpeg означали бы два кодирования разом на машине,
    где память кончалась трижды. Второй ролик при занятой записи ждёт
    своей очереди, а не собирается параллельно."""
    assert client.post(f"/api/videos/{VIDEO}/shorts/0/render").status_code == 200
    assert client.post(f"/api/videos/{VIDEO}/shorts/1/render").status_code == 409


def test_busy_record_refuses_a_different_job(client, video) -> None:
    """Очередь не плодит задачи на одну запись — но и не подменяет одну
    работу другой молча: раньше «пересобрать ролик» во время разбора
    не делало ничего, а интерфейс показывал «в очереди»."""
    client.post(f"/api/videos/{VIDEO}/run", json={"group": "analysis"})

    refused = client.post(f"/api/videos/{VIDEO}/shorts/1/render")
    assert refused.status_code == 409
    assert "разбор записи" in refused.json()["detail"]

    other = client.post(f"/api/videos/{VIDEO}/run", json={"group": "long"})
    assert other.status_code == 409


def test_same_job_pressed_twice_is_not_an_error(client, video) -> None:
    """Двойной клик по той же кнопке — обычное дело, а не ошибка."""
    first = client.post(f"/api/videos/{VIDEO}/run", json={"group": "analysis"})
    second = client.post(f"/api/videos/{VIDEO}/run", json={"group": "analysis"})
    assert first.status_code == second.status_code == 200
    assert second.json()["started"] is False


def test_model_catalogue_is_not_fetched_on_every_open(client, monkeypatch) -> None:
    """Каталог моделей лежит у поставщика и меняется раз в дни.

    Экран настроек спрашивал его при каждом открытии записи, а пока работа
    ждала очереди — каждые три секунды: запрос на чужой сервер за списком,
    который не менялся.
    """
    from narezka.core import llm  # noqa: PLC0415

    api_app._MODEL_CATALOGUE.clear()
    calls = []
    monkeypatch.setattr(llm, "fetch_models", lambda *a, **kw: calls.append(1) or [])

    assert client.get("/api/settings/models").status_code == 200
    assert client.get("/api/settings/models").status_code == 200

    assert len(calls) == 1, "каталог запрашивается заново на каждое открытие"
