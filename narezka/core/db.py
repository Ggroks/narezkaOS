"""Хранилище данных о результатах клипов.

BAZA.md §31 и §63. SQLite, а не сервер БД: проект local-first (§27), а файл
рядом с артефактами переносится вместе с ними и не требует ничего запускать.
Схема совместима с переездом на PostgreSQL — типы простые, специфики нет.

Смысл всей таблицы — в одном: **связать признаки клипа с тем, что он собрал
на публикации**. Без этой связи цифры просмотров ничему не учат, потому что
непонятно, какие именно признаки сработали.

Отсюда три требования, каждое из которых легко потерять:

1. **Вектор признаков замораживается при публикации.** Кандидат
   пересчитывается при каждом изменении стадии, промпт правится, веса
   меняются. Через месяц по живым артефактам уже не восстановить, какие
   числа стояли в момент, когда клип уходил в публикацию.
2. **Версии схемы и промпта пишутся рядом.** Иначе непонятно, на какой
   версии системы получен результат, и данные разных версий смешиваются
   в одну кучу.
3. **Дата снятия метрик обязательна.** Тысяча просмотров за сутки и та же
   тысяча за месяц — разные результаты, и без даты их не различить.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_NAME = "narezka.db"

SCHEMA_VERSION = 1

#: Платформы, для которых имеет смысл хранить метрики. Список открытый:
#: значение не проверяется на уровне БД, чтобы добавление площадки не
#: требовало миграции.
PLATFORMS = ("youtube", "tiktok", "instagram", "vk", "other")

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id        TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    title           TEXT,
    -- §31: закладывается сразу — потом это миграция, а не правка документа.
    content_origin  TEXT NOT NULL DEFAULT 'own',
    retention_until TEXT,
    created_at      TEXT NOT NULL
);

-- Снимок клипа на момент публикации. Неизменяемый по смыслу: строка пишется
-- один раз и потом только читается.
CREATE TABLE IF NOT EXISTS clips (
    clip_id              TEXT PRIMARY KEY,
    video_id             TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    clip_index           INTEGER NOT NULL,
    start_seconds        REAL NOT NULL,
    end_seconds          REAL NOT NULL,
    title                TEXT,
    interest_score       REAL,
    -- Вектор признаков и штрафы в том виде, в каком они были при публикации.
    factors_snapshot     TEXT NOT NULL,
    penalties_snapshot   TEXT,
    weights_snapshot     TEXT,
    score_schema_version INTEGER,
    prompt_version       INTEGER,
    model                TEXT,
    -- Решение человека на обзоре: быстрый сигнал, копится с первого дня.
    human_verdict        TEXT,
    -- Насколько границы подвинул **человек** относительно того, что предложила
    -- система (§63). Именно его правка показывает систематическую ошибку
    -- определения границ; уточнение модели относительно сырого кандидата —
    -- другой сигнал, он восстанавливается из selection.json.
    bounds_shift_start   REAL,
    bounds_shift_end     REAL,
    published_at         TEXT,
    platform             TEXT,
    url                  TEXT,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clip_performance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id     TEXT NOT NULL REFERENCES clips(clip_id) ON DELETE CASCADE,
    platform    TEXT NOT NULL,
    -- Обязательна: тысяча просмотров за сутки и за месяц — разные результаты.
    measured_at TEXT NOT NULL,
    views       INTEGER,
    likes       INTEGER,
    comments    INTEGER,
    shares      INTEGER,
    retention   REAL,
    ctr         REAL,
    note        TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clips_video ON clips(video_id);
CREATE INDEX IF NOT EXISTS idx_perf_clip ON clip_performance(clip_id, measured_at);

CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def db_path(storage_root: Path) -> Path:
    return Path(storage_root) / DB_NAME


@contextmanager
def connect(storage_root: Path):
    """Подключение с созданной схемой.

    Схема применяется при каждом открытии: все выражения идемпотентны,
    поэтому отдельный шаг миграции для первой версии не нужен.
    """
    path = db_path(storage_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        if not connection.execute("SELECT version FROM schema_meta").fetchone():
            connection.execute("INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,))
        connection.commit()
        yield connection
        connection.commit()
    finally:
        connection.close()


def upsert_video(
    connection: sqlite3.Connection,
    *,
    video_id: str,
    project_id: str,
    title: str | None = None,
    content_origin: str = "own",
    retention_until: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO videos (video_id, project_id, title, content_origin, retention_until, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            project_id = excluded.project_id,
            title = COALESCE(excluded.title, videos.title),
            content_origin = excluded.content_origin,
            retention_until = excluded.retention_until
        """,
        (video_id, project_id, title, content_origin, retention_until, now()),
    )


def freeze_clip(
    connection: sqlite3.Connection,
    *,
    clip: dict[str, Any],
    video_id: str,
    weights: dict[str, float] | None = None,
    title: str | None = None,
    platform: str | None = None,
    url: str | None = None,
    published_at: str | None = None,
    human_verdict: str | None = None,
    bounds_shift: tuple[float | None, float | None] = (None, None),
) -> str:
    """Записывает клип в неизменяемом виде.

    Повторный вызов **не перезаписывает** снимок признаков: он и должен
    остаться таким, каким был при публикации. Обновляются только сведения
    о самой публикации — площадка, ссылка, дата.
    """
    clip_id = clip["clip_id"]

    connection.execute(
        """
        INSERT INTO clips (
            clip_id, video_id, clip_index, start_seconds, end_seconds, title,
            interest_score, factors_snapshot, penalties_snapshot, weights_snapshot,
            score_schema_version, prompt_version, model, human_verdict,
            bounds_shift_start, bounds_shift_end, published_at, platform, url, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(clip_id) DO UPDATE SET
            published_at = COALESCE(excluded.published_at, clips.published_at),
            platform = COALESCE(excluded.platform, clips.platform),
            url = COALESCE(excluded.url, clips.url),
            title = COALESCE(excluded.title, clips.title),
            human_verdict = COALESCE(excluded.human_verdict, clips.human_verdict)
        """,
        (
            clip_id,
            video_id,
            clip.get("index", 0),
            clip["start"],
            clip["end"],
            title,
            clip.get("interest_score"),
            json.dumps(clip.get("factors") or {}, ensure_ascii=False),
            json.dumps(clip.get("penalties") or {}, ensure_ascii=False),
            json.dumps(weights or {}, ensure_ascii=False),
            clip.get("score_schema_version"),
            clip.get("prompt_version"),
            clip.get("model"),
            human_verdict,
            bounds_shift[0],
            bounds_shift[1],
            published_at,
            platform,
            url,
            now(),
        ),
    )
    return clip_id


def add_measurement(
    connection: sqlite3.Connection,
    *,
    clip_id: str,
    platform: str,
    measured_at: str | None = None,
    views: int | None = None,
    likes: int | None = None,
    comments: int | None = None,
    shares: int | None = None,
    retention: float | None = None,
    ctr: float | None = None,
    note: str | None = None,
) -> int:
    """Добавляет замер. Замеры не перезаписываются, а копятся.

    История важнее последнего значения: рост за неделю говорит больше,
    чем итоговое число, а исправить опечатку можно удалением строки.
    """
    if not connection.execute("SELECT 1 FROM clips WHERE clip_id = ?", (clip_id,)).fetchone():
        raise ValueError(f"клип {clip_id} не зафиксирован — сначала опубликуйте его")

    cursor = connection.execute(
        """
        INSERT INTO clip_performance (
            clip_id, platform, measured_at, views, likes, comments, shares,
            retention, ctr, note, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clip_id, platform, measured_at or now(), views, likes, comments,
            shares, retention, ctr, note, now(),
        ),
    )
    return int(cursor.lastrowid or 0)


def clip_rows(connection: sqlite3.Connection, video_id: str | None = None) -> list[dict[str, Any]]:
    """Клипы вместе с последним замером по каждому."""
    query = """
        SELECT c.*,
               p.views, p.likes, p.comments, p.shares, p.retention, p.ctr,
               p.measured_at, p.platform AS measured_platform
        FROM clips c
        LEFT JOIN clip_performance p ON p.id = (
            SELECT id FROM clip_performance
            WHERE clip_id = c.clip_id
            ORDER BY measured_at DESC, id DESC
            LIMIT 1
        )
    """
    params: tuple[Any, ...] = ()
    if video_id:
        query += " WHERE c.video_id = ?"
        params = (video_id,)
    query += " ORDER BY c.created_at DESC"

    return [_row_to_dict(row) for row in connection.execute(query, params).fetchall()]


def measurements(connection: sqlite3.Connection, clip_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM clip_performance WHERE clip_id = ? ORDER BY measured_at, id",
        (clip_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for field in ("factors_snapshot", "penalties_snapshot", "weights_snapshot"):
        if data.get(field):
            try:
                data[field] = json.loads(data[field])
            except ValueError:
                data[field] = {}
    return data
