"""Регистрация видео — общий слой для CLI и интерфейса (BAZA.md §33).

Проверяется то, из-за чего модуль и появился: одинаковый идентификатор
у одного и того же источника, своё название рядом с заголовком источника
и неизменная дата добавления. Расхождение здесь стоит дорого — два каталога
на одну запись и разошедшаяся обработка.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from narezka.core import registry
from narezka.core.paths import video_paths

URL = "https://twitch.tv/videos/123"


def metadata_of(root: Path, video_id: str) -> dict:
    paths = video_paths(root, "default", video_id)
    return json.loads(paths.metadata.read_text(encoding="utf-8"))


def media_file(path: Path, payload: bytes = b"\x00" * 4096) -> Path:
    path.write_bytes(payload)
    return path


def test_same_url_gives_same_video(tmp_path: Path) -> None:
    first = registry.register(storage_root=tmp_path, project="default", url=URL)
    second = registry.register(storage_root=tmp_path, project="default", url=URL)
    assert first.video_id == second.video_id
    assert first.created and not second.created


def test_same_file_under_another_name_is_one_video(tmp_path: Path) -> None:
    """Идентификатор считается по содержимому, а не по имени.

    Иначе переименованная копия заводит второе видео и вся обработка идёт
    заново — на многочасовой записи это часы работы впустую.
    """
    payload = b"NAREZKA" * 1000
    first = registry.register(
        storage_root=tmp_path, project="default",
        file=media_file(tmp_path / "stream.mp4", payload),
    )
    second = registry.register(
        storage_root=tmp_path, project="default",
        file=media_file(tmp_path / "копия стрима.mp4", payload),
    )
    assert first.video_id == second.video_id


def test_title_is_stored_and_wins_over_source(tmp_path: Path) -> None:
    result = registry.register(
        storage_root=tmp_path, project="default", url=URL, title="  Стрим  про  котов  "
    )
    data = metadata_of(tmp_path, result.video_id)
    # Лишние пробелы схлопываются: имя попадает в карточку каталога.
    assert data["title"] == "Стрим про котов"
    data["source_title"] = "VOD 4821 [RU]"
    assert registry.display_title(data, result.video_id) == "Стрим про котов"


def test_without_title_source_heading_is_used(tmp_path: Path) -> None:
    result = registry.register(storage_root=tmp_path, project="default", url=URL)
    data = metadata_of(tmp_path, result.video_id)
    assert "title" not in data
    assert registry.display_title({**data, "source_title": "VOD 4821"}, "id") == "VOD 4821"


def test_url_stands_in_for_the_name_until_download(tmp_path: Path) -> None:
    """Пока заголовка нет, в карточке ссылка, а не `eeb8e3e09536`.

    Идентификатор человеку не говорит ничего, а ссылка говорит, какая
    это запись.
    """
    result = registry.register(
        storage_root=tmp_path, project="default", url="https://www.twitch.tv/videos/7/"
    )
    data = metadata_of(tmp_path, result.video_id)
    assert registry.display_title(data, result.video_id) == "twitch.tv/videos/7"


def test_added_date_does_not_move_on_second_add(tmp_path: Path) -> None:
    """Повторное добавление не двигает запись в начало каталога."""
    result = registry.register(storage_root=tmp_path, project="default", url=URL)
    first = metadata_of(tmp_path, result.video_id)["created_at"]
    registry.register(storage_root=tmp_path, project="default", url=URL, title="Другое имя")
    again = metadata_of(tmp_path, result.video_id)
    assert again["created_at"] == first
    assert again["title"] == "Другое имя"


def test_rename_back_to_source_title(tmp_path: Path) -> None:
    result = registry.register(storage_root=tmp_path, project="default", url=URL, title="Своё")
    paths = video_paths(tmp_path, "default", result.video_id)

    data = json.loads(paths.metadata.read_text(encoding="utf-8"))
    data["source_title"] = "Заголовок площадки"
    paths.metadata.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    assert registry.rename(paths, "") == "Заголовок площадки"
    assert "title" not in json.loads(paths.metadata.read_text(encoding="utf-8"))


def test_bad_input_is_refused(tmp_path: Path) -> None:
    with pytest.raises(registry.RegistrationError):
        registry.register(storage_root=tmp_path, project="default")
    with pytest.raises(registry.RegistrationError):
        registry.register(storage_root=tmp_path, project="default", url=URL, file=tmp_path / "x")
    with pytest.raises(registry.RegistrationError):
        registry.register(storage_root=tmp_path, project="default", file=tmp_path / "нет.mp4")
    with pytest.raises(registry.RegistrationError):
        registry.register(
            storage_root=tmp_path, project="default", url=URL, title="я" * (registry.TITLE_LIMIT + 1)
        )


def test_local_file_stays_where_it_was(tmp_path: Path) -> None:
    """Исходник не копируется: ссылка вместо десятков гигабайт (§65)."""
    source = media_file(tmp_path / "stream.mp4")
    result = registry.register(storage_root=tmp_path, project="default", file=source)
    placed = video_paths(tmp_path, "default", result.video_id).source / "stream.mp4"
    assert placed.is_file() and source.is_file()
    assert result.placement in {"жёсткая ссылка", "символическая ссылка", "копия"}


def test_second_copy_does_not_break_the_record(tmp_path: Path) -> None:
    """Одна запись — один исходник.

    Найдено живым прогоном: повторная загрузка того же файла положила
    в запись второй экземпляр под другим именем, и `find_source` отказался
    работать — «больше одного медиафайла». Идентификатор считается по
    содержимому, значит это тот же материал, и класть его второй раз незачем.
    """
    payload = b"NAREZKA" * 2000
    first = registry.register(
        storage_root=tmp_path, project="default",
        file=media_file(tmp_path / "stream.mp4", payload),
    )
    registry.register(
        storage_root=tmp_path, project="default",
        file=media_file(tmp_path / "стрим-копия.mp4", payload),
    )

    source = video_paths(tmp_path, "default", first.video_id).source
    assert len([p for p in source.iterdir() if p.is_file()]) == 1

    from narezka.core.media import find_source

    assert find_source(source).is_file()
