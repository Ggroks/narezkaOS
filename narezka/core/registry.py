"""Регистрация видео в проекте: один слой для CLI и для интерфейса.

BAZA.md §33 требует, чтобы операция не жила только в одном месте. До этого
модуля регистрация была написана дважды — в `cli.add` и в `POST /api/videos`, —
и версии успели разойтись в главном: идентификатор одного и того же файла
считался по-разному, поэтому файл, добавленный из консоли и из браузера,
превращался в два разных видео с раздельной обработкой. Здесь версия одна.

**О названии.** Интерфейс называет запись «проектом», а код — «видео»: так
сущность звалась с самого начала, и переименовывать её в хранилище, в путях,
в базе и в тридцати модулях ради слова на экране незачем. Расхождение
намеренное, и это единственное место, где о нём нужно помнить: `title` —
имя, которое дал человек, `source_title` — заголовок, взятый из источника.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from narezka.core import sources
from narezka.core.artifacts import Artifact
from narezka.core.media import MEDIA_SUFFIXES
from narezka.core.paths import VideoPaths, video_paths

#: Предел длины имени. Не ограничение хранилища, а забота о раскладке: строка
#: в карточку каталога всё равно не поместится, а обрезать её молча — хуже,
#: чем не принять.
TITLE_LIMIT = 200


class RegistrationError(ValueError):
    """Видео зарегистрировать нельзя, и причина понятна человеку."""


@dataclass(frozen=True)
class Registration:
    video_id: str
    #: Запись создана впервые. Повторное добавление того же источника не
    #: создаёт дубль — оно возвращает уже существующее видео.
    created: bool
    #: Как исходник оказался в хранилище: «жёсткая ссылка», «будет скачано»…
    placement: str
    title: str


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def video_id_for_url(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()[:12]


def video_id_for_file(path: Path) -> str:
    """Идентификатор по содержимому: тот же файл — тот же id.

    Считается по размеру и по краям файла, а не по имени: переименованная
    копия не должна заводить второе видео, а читать целиком многогигабайтную
    запись ради контрольной суммы — минуты работы на пустом месте.
    """
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(1024 * 1024))
        if size > 2 * 1024 * 1024:
            handle.seek(-1024 * 1024, os.SEEK_END)
            digest.update(handle.read(1024 * 1024))
    return digest.hexdigest()[:12]


def place_source(src: Path, dst: Path) -> str:
    """Жёсткая ссылка, иначе символическая, иначе копия.

    Копировать десятки гигабайт ради того, чтобы файл лежал «внутри»
    хранилища, смысла нет (§65).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return "уже на месте"
    try:
        os.link(src, dst)
        return "жёсткая ссылка"
    except OSError:
        pass
    try:
        dst.symlink_to(src.resolve())
        return "символическая ссылка"
    except OSError:
        pass
    shutil.copy2(src, dst)
    return "копия"


def clean_title(title: str | None) -> str | None:
    """Имя без краевых пробелов. Пустая строка означает «имени нет»."""
    if title is None:
        return None
    cleaned = " ".join(title.split())
    if not cleaned:
        return None
    if len(cleaned) > TITLE_LIMIT:
        raise RegistrationError(f"название длиннее {TITLE_LIMIT} символов")
    return cleaned


def short_url(url: str) -> str:
    """Ссылка без обвязки: `https://www.twitch.tv/videos/7` → `twitch.tv/videos/7`."""
    trimmed = url.strip().removeprefix("https://").removeprefix("http://")
    return trimmed.removeprefix("www.").rstrip("/")


def display_title(metadata: dict[str, Any], video_id: str) -> str:
    """Имя для показа: своё важнее взятого из источника.

    Заголовок из источника («Стрим #482 [VOD]») пишет площадка, а не человек,
    и в каталоге из десяти записей такие имена не различаются.

    Последняя ступень — ссылка, и только потом идентификатор: пока запись не
    скачана, заголовка ещё нет, а `eeb8e3e09536` в карточке каталога не
    говорит человеку ничего.
    """
    origin = metadata.get("origin") or {}
    return (
        metadata.get("title")
        or metadata.get("source_title")
        or metadata.get("source_file")
        or (short_url(origin["url"]) if origin.get("type") == "url" and origin.get("url") else None)
        or video_id
    )


def register(
    *,
    storage_root: Path,
    project: str,
    url: str | None = None,
    file: Path | None = None,
    title: str | None = None,
    allowed_hosts: tuple[str, ...] | list[str] = (),
    allow_local_paths: bool = True,
) -> Registration:
    """Заводит видео по ссылке или по локальному файлу.

    Повторный вызов с тем же источником не создаёт дубль: идентификатор
    выводится из самого источника. Название при этом обновляется — человек
    мог добавить запись второй раз именно ради того, чтобы её переназвать.
    """
    if (url is None) == (file is None):
        raise RegistrationError("укажите ровно одно: ссылку или файл")

    name = clean_title(title)

    if file is not None:
        if not allow_local_paths:
            # На сервере это чтение его собственного диска: путь приходит
            # от постороннего, а читает его процесс с правами сервиса.
            raise RegistrationError("файл нужно загрузить, а не указать путём на сервере")
        source = Path(file).expanduser()
        if not source.is_file():
            raise RegistrationError(f"файл не найден: {source}")
        video_id = video_id_for_file(source)
        origin: dict[str, Any] = {"type": "local_file", "path": str(source.resolve())}
    else:
        assert url is not None
        address = url.strip()
        if not address:
            raise RegistrationError("пустая ссылка")
        try:
            address = sources.check(address, allowed_hosts=allowed_hosts)
        except sources.SourceError as exc:
            raise RegistrationError(str(exc)) from exc
        video_id = video_id_for_url(address)
        origin = {"type": "url", "url": address}

    paths = video_paths(storage_root, project, video_id)
    created = not paths.exists()
    paths.ensure()

    placement = (
        place_source(Path(file).expanduser(), paths.source / Path(file).name)
        if file is not None
        else "будет скачано"
    )

    metadata = Artifact(paths.metadata)
    existing = metadata.read_json() if metadata.exists() else {}
    existing.update(
        {
            "video_id": video_id,
            "project_id": project,
            "origin": origin,
            # §31: поля закладываются сразу, чтобы потом не мигрировать схему.
            "content_origin": "own",
            "retention_until": None,
        }
    )
    if name:
        existing["title"] = name
    # Дата добавления ставится один раз: повторное добавление того же
    # источника не должно двигать запись в начало каталога.
    existing.setdefault("created_at", now())
    metadata.write_json(existing)

    return Registration(
        video_id=video_id,
        created=created,
        placement=placement,
        title=display_title(existing, video_id),
    )


def rename(paths: VideoPaths, title: str | None) -> str:
    """Меняет своё название записи. Пустое — возврат к заголовку источника.

    Отдельная операция, а не поле при создании: имя проекту чаще всего
    придумывают, уже посмотрев, что в записи, — то есть после обработки.
    """
    metadata = Artifact(paths.metadata)
    existing = metadata.read_json() if metadata.exists() else {}
    name = clean_title(title)
    if name:
        existing["title"] = name
    else:
        existing.pop("title", None)
    metadata.write_json(existing)
    return display_title(existing, paths.video_id)


def known_suffix(path: Path) -> bool:
    """Похоже ли расширение на медиафайл. Не запрет, а повод предупредить."""
    return path.suffix.lower() in MEDIA_SUFFIXES
