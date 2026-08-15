"""Стадия chat: повтор чата Twitch VOD.

BAZA.md §41. В плане v1 чат стоял на шестом этапе — он перенесён сюда,
потому что это самый дешёвый сигнал из всех: считается на CPU, ничего
не стоит и не требует модели, а по силе сравним с громкостью.

Стадия опциональна (§43) сразу по двум причинам: источник может быть
не с Twitch, а сам эндпоинт повтора неофициальный и однажды пропадёт.
Ни то, ни другое не должно ронять пайплайн.
"""

from __future__ import annotations

from typing import Any

from narezka.core import twitch
from narezka.core.artifacts import Artifact
from narezka.core.stage import Device, Stage, StageContext, StageSkipped

CHAT_NAME = "chat.json"

#: Насколько близко к концу записи отказ считается нормальным завершением,
#: а не обрывом. Последние сообщения приходят с запозданием, поэтому точного
#: совпадения с длительностью не бывает.
CHAT_TAIL_TOLERANCE = 60.0


class ChatStage(Stage):
    name = "chat"
    #: v3 — повторы при временном отказе и различение конца записи от обрыва.
    version = 3
    device = Device.ANY
    optional = True
    description = "Повтор чата Twitch: сообщения с привязкой к времени записи"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.metadata)]

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.analysis / CHAT_NAME)]

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        return {"source": "twitch_gql"}

    def check_available(self, ctx: StageContext) -> str | None:
        metadata = Artifact(ctx.paths.metadata)
        if not metadata.exists():
            return "нет сведений об источнике"
        origin = metadata.read_json().get("origin") or {}
        if origin.get("type") != "url":
            return "источник не по ссылке — чата нет"
        if twitch.video_id_from_url(origin.get("url", "")) is None:
            return "источник не Twitch VOD — чата нет"
        return None

    def run(self, ctx: StageContext) -> None:
        origin = Artifact(ctx.paths.metadata).read_json().get("origin") or {}
        video_id = twitch.video_id_from_url(origin.get("url", ""))
        if video_id is None:
            raise StageSkipped("не удалось разобрать ссылку на запись Twitch")

        duration = Artifact(ctx.paths.metadata).read_json().get("duration_seconds")
        ctx.log.info("читаю чат записи %s", video_id)

        messages: list[dict[str, Any]] = []
        try:
            for message in twitch.fetch_messages(
                video_id,
                duration_seconds=duration,
                on_progress=lambda count, at: ctx.log.info(
                    "сообщений %d, дошли до %.0f с…", count, at
                ),
            ):
                messages.append(message)
        except twitch.TwitchError as exc:
            # Эндпоинт неофициальный: его отказ — ожидаемое событие, а не сбой
            # программы. Уже прочитанное сохраняем, если оно есть.
            if not messages:
                raise StageSkipped(f"чат недоступен: {exc}") from exc

            reached = messages[-1]["at"]
            # Отказ за концом записи — это и есть конец, а не потеря данных.
            # Без длительности (probe ещё не отработал) остановиться не по чему,
            # и последний запрос неизбежно уходит за край.
            if duration and reached >= duration - CHAT_TAIL_TOLERANCE:
                ctx.log.info("дочитано до конца записи (%.0f с)", reached)
            else:
                ctx.log.warning(
                    "чат оборвался на %.0f с из %s: %s",
                    reached, f"{duration:.0f}" if duration else "неизвестной длительности", exc,
                )

        if not messages:
            raise StageSkipped("в записи нет чата")

        bots = sum(1 for m in messages if m["is_bot"])
        authors = len({m["author"] for m in messages if not m["is_bot"]})
        span = messages[-1]["at"] - messages[0]["at"]

        Artifact(ctx.paths.analysis / CHAT_NAME).write_json(
            {
                "video_id": video_id,
                "messages": messages,
                "stats": {
                    "total": len(messages),
                    "from_bots": bots,
                    "authors": authors,
                    "span_seconds": round(span, 1),
                    "per_minute": round(len(messages) / (span / 60), 1) if span > 0 else 0.0,
                },
            }
        )

        ctx.log.info(
            "сообщений %d от %d человек, из них ботами %d, в среднем %.1f в минуту",
            len(messages), authors, bots,
            len(messages) / (span / 60) if span > 0 else 0.0,
        )
