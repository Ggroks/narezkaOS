"""Стадия metadata: тексты для публикации.

BAZA.md §22, §23, §24. Заголовок, описание и хэштеги для каждого отобранного
клипа. Модель предлагает варианты заголовка, а выбирает система по
проверяемым признакам — см. narezka/core/publish.py.

Стадия опциональна (§43) и идёт последней: тексты нужны только для готовых
роликов, и без ключа пайплайн доходит до видео без них.
"""

from __future__ import annotations

from typing import Any

from narezka.core import llm, prompts, publish
from narezka.core.artifacts import Artifact
from narezka.core.clips import SELECTION_NAME, describe_source, load_clips
from narezka.core.stage import Device, Stage, StageContext, StageSkipped
from narezka.stages.llm_select import BATCH_SIZE, parse_reply, transcript_lines
from narezka.stages.transcribe import TRANSCRIPT_NAME

PUBLISH_NAME = "publish.json"


class MetadataStage(Stage):
    name = "metadata"
    #: v2 — клипы объявлены входом: без этого тексты брались из кэша после
    #: пересчёта отбора и относились к другим границам.
    version = 2
    device = Device.ANY
    optional = True
    description = "Заголовок, описание и хэштеги для каждого клипа"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        # Клипы обязаны быть входом: тексты пишутся под конкретные границы,
        # и без этой связи стадия бралась из кэша после пересчёта отбора,
        # оставляя заголовки от других клипов.
        inputs = [Artifact(ctx.paths.transcript / TRANSCRIPT_NAME)]
        selection = Artifact(ctx.paths.analysis / SELECTION_NAME)
        inputs.append(
            selection if selection.exists() else Artifact(ctx.paths.analysis / "candidates.json")
        )
        return inputs

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.analysis / PUBLISH_NAME)]

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        return {
            **ctx.config.llm.model_dump(),
            "prompt_version": prompts.PROMPT_VERSION,
            "max_title_chars": publish.MAX_TITLE_CHARS,
            "max_hashtags": publish.MAX_HASHTAGS,
        }

    def run(self, ctx: StageContext) -> None:
        key = llm.api_key(provider_name=ctx.config.llm.provider)
        if not key:
            raise StageSkipped(
                f"нет ключа {llm.provider(ctx.config.llm.provider).key_env} — тексты для публикации не сгенерированы"
            )
        if not ctx.config.llm.model:
            raise StageSkipped("в конфиге не выбрана модель (llm.model)")

        clips, source = load_clips(ctx.paths)
        if not clips:
            raise StageSkipped("нет клипов — тексты не для чего писать")
        ctx.log.info(describe_source(source, len(clips)))

        transcript = Artifact(ctx.paths.transcript / TRANSCRIPT_NAME).read_json()
        chain = [ctx.config.llm.model, *ctx.config.llm.fallback_models]

        answers: dict[int, dict[str, Any]] = {}
        models_used: set[str] = set()
        failures: list[str] = []

        for offset in range(0, len(clips), BATCH_SIZE):
            batch = clips[offset : offset + BATCH_SIZE]
            ctx.log.info("пакет %d–%d из %d", offset + 1, offset + len(batch), len(clips))
            try:
                answered, model = self._ask(ctx, key, chain, batch, transcript)
            except (llm.LlmError, ValueError) as exc:
                failures.append(f"пакет {offset // BATCH_SIZE + 1}: {exc}")
                ctx.log.warning("пакет пропущен — %s", exc)
                continue
            answers.update(answered)
            models_used.add(model)

        if not answers:
            raise StageSkipped("ни один пакет не обработан: " + "; ".join(failures[:3]))

        entries = [
            publish.build_entry(clip, answers[clip["index"]])
            for clip in clips
            if clip["index"] in answers
        ]

        rejected = sum(
            1 for entry in entries for variant in entry["title_variants"] if variant["problems"]
        )

        Artifact(ctx.paths.analysis / PUBLISH_NAME).write_json(
            {
                "video_id": ctx.video_id,
                "prompt_version": prompts.PROMPT_VERSION,
                "models": sorted(models_used),
                "clips": entries,
                "stats": {
                    "clips": len(clips),
                    "written": len(entries),
                    "titles_rejected": rejected,
                    "batches_failed": len(failures),
                },
                "failures": failures,
            }
        )

        ctx.log.info(
            "тексты для %d клипов, вариантов заголовка отбраковано %d",
            len(entries), rejected,
        )
        for entry in entries[:3]:
            ctx.log.info("  #%d «%s» %s", entry["index"], entry["title"],
                         " ".join(entry["hashtags"][:4]))

    def _ask(
        self,
        ctx: StageContext,
        key: str,
        chain: list[str],
        batch: list[dict[str, Any]],
        transcript: dict[str, Any],
    ) -> tuple[dict[int, dict[str, Any]], str]:
        blocks = [
            prompts.render_clip_for_metadata(
                clip, transcript_lines(transcript, clip["start"], clip["end"])
            )
            for clip in batch
        ]

        response = llm.chat(
            key,
            chain,
            {
                "messages": [
                    {"role": "system", "content": prompts.METADATA_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": "Напиши тексты для фрагментов ниже.\n\n" + "\n\n".join(blocks),
                    },
                ],
                "response_format": prompts.metadata_schema(),
                # Чуть выше, чем при отборе: заголовки должны быть разными,
                # а не тремя перефразировками одной мысли.
                "temperature": 0.7,
            },
            timeout=ctx.config.llm.timeout_seconds,
            max_retries=ctx.config.llm.max_retries,
            provider_name=ctx.config.llm.provider,
        )

        choices = response.get("choices") or []
        content = choices[0].get("message", {}).get("content", "") if choices else ""
        known = {clip["index"] for clip in batch}
        answered = {
            int(item["index"]): item
            for item in parse_reply(content)
            if isinstance(item.get("index"), int) and int(item["index"]) in known
        }
        return answered, response.get("model") or chain[0]
