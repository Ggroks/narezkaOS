"""Стадия llm_select: отбор моментов моделью.

BAZA.md §11, §12, §14, §15, §26. Второй проход воронки: дешёвые сигналы уже
сузили восемь часов до сотни кандидатов, и только их читает модель.

Три решения, заданные спецификацией и легко теряемые при реализации:

1. **Границы уточняются здесь же, до расчёта оценки** (§4, §14). Сдвинув
   границы, мы меняем содержимое клипа — оценка, посчитанная до сдвига,
   относится к другому клипу.
2. **Оценку считает код, а не модель** (§12). Модель возвращает факторы,
   `interest_score` выводится из них по весам конфига. Иначе веса ни на что
   не влияют, а отладить оценку нельзя.
3. **Стадия опциональная** (§43). Нет ключа — пайплайн доходит до готовых
   роликов на кандидатах от дешёвых сигналов, а не падает.
"""

from __future__ import annotations

import json
from typing import Any

from narezka.core import llm, prompts
from narezka.core.artifacts import Artifact
from narezka.core.scoring import build_clip, select_top
from narezka.core.stage import Device, Stage, StageContext, StageSkipped
from narezka.stages.candidates import CANDIDATES_NAME
from narezka.stages.transcribe import TRANSCRIPT_NAME

SELECTION_NAME = "selection.json"

#: Сколько фрагментов уходит в один запрос. Пакет вдвое дешевле поштучных
#: вызовов (§25), но слишком большой пакет размывает внимание модели —
#: она начинает оценивать бегло и одинаково.
BATCH_SIZE = 6

#: Запас контекста вокруг окна кандидата. Нужен, чтобы модель могла отодвинуть
#: начало к завязке: §14 прямо запрещает брать «событие минус десять секунд»,
#: а без реплик до окна двигать границу некуда.
CONTEXT_MARGIN = 20.0


def transcript_lines(
    transcript: dict[str, Any], start: float, end: float
) -> list[dict[str, Any]]:
    """Реплики, попадающие в окно с запасом. Подозрительные отброшены (§57)."""
    lines = []
    for segment in transcript.get("segments", []):
        if segment.get("suspect"):
            continue
        if segment.get("end", 0) < start or segment.get("start", 0) > end:
            continue
        lines.append({"start": segment["start"], "text": (segment.get("text") or "").strip()})
    return lines


def parse_reply(content: str) -> list[dict[str, Any]]:
    """Разбирает ответ модели.

    Схема ответа задана в запросе, но не все модели соблюдают её строго:
    часть оборачивает JSON в markdown-заборчик. Это дешевле снять здесь,
    чем терять весь пакет.
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json").strip()

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"ответ не разбирается как JSON: {exc}") from exc

    clips = data.get("clips") if isinstance(data, dict) else data
    if not isinstance(clips, list):
        raise ValueError("в ответе нет списка clips")
    return [c for c in clips if isinstance(c, dict)]


class LlmSelectStage(Stage):
    name = "llm_select"
    #: v2 — уточнённые границы проверяются против ограничений площадки:
    #: модель о них не знает и однажды ужала клип до шести секунд (§17).
    #: v3 — упавшие пакеты повторяются, а не теряются вместе с оценками.
    version = 3
    device = Device.ANY
    optional = True
    description = "Отбор моментов моделью: оценка, уточнение границ, топ-N"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        return [
            Artifact(ctx.paths.analysis / CANDIDATES_NAME),
            Artifact(ctx.paths.transcript / TRANSCRIPT_NAME),
        ]

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.analysis / SELECTION_NAME)]

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        return {
            **ctx.config.llm.model_dump(),
            **ctx.config.score.model_dump(),
            "max_clips": ctx.config.funnel.max_clips,
            "prompt_version": prompts.PROMPT_VERSION,
        }

    def run(self, ctx: StageContext) -> None:
        key = llm.api_key(provider_name=ctx.config.llm.provider)
        if not key:
            raise StageSkipped(
                f"нет ключа {llm.provider(ctx.config.llm.provider).key_env} — отбор моделью пропущен, "
                "ролики соберутся по кандидатам от дешёвых сигналов"
            )
        if not ctx.config.llm.model:
            raise StageSkipped("в конфиге не выбрана модель (llm.model)")

        candidates = Artifact(ctx.paths.analysis / CANDIDATES_NAME).read_json().get("candidates", [])
        if not candidates:
            raise StageSkipped("кандидатов нет")

        transcript = Artifact(ctx.paths.transcript / TRANSCRIPT_NAME).read_json()
        chain = [ctx.config.llm.model, *ctx.config.llm.fallback_models]

        numbered = list(enumerate(candidates))
        batches = [numbered[i : i + BATCH_SIZE] for i in range(0, len(numbered), BATCH_SIZE)]
        verdicts, models_used, failures = llm.process_batches(
            batches,
            lambda batch: self._ask(ctx, key, chain, batch, transcript),
            ctx.log,
        )

        if not verdicts:
            raise StageSkipped("ни один пакет не оценён: " + "; ".join(failures[:3]))

        weights = ctx.config.score.weights
        clips = [
            build_clip(
                video_id=ctx.video_id,
                index=index,
                candidate=candidate,
                verdict=verdicts[index],
                weights=weights,
                schema_version=ctx.config.score.schema_version,
                model=", ".join(sorted(models_used)),
                min_duration=ctx.config.output.short.min_duration,
                max_duration=ctx.config.output.short.max_duration,
            )
            for index, candidate in enumerate(candidates)
            if index in verdicts
        ]

        top = select_top(clips, ctx.config.funnel.max_clips)

        Artifact(ctx.paths.analysis / SELECTION_NAME).write_json(
            {
                "video_id": ctx.video_id,
                "score_schema_version": ctx.config.score.schema_version,
                "prompt_version": prompts.PROMPT_VERSION,
                "models": sorted(models_used),
                "weights": weights,
                "clips": top,
                "stats": {
                    "candidates": len(candidates),
                    "scored": len(clips),
                    "selected": len(top),
                    "batches_failed": len(failures),
                    "mean_score": round(
                        sum(c["interest_score"] for c in clips) / len(clips), 4
                    ) if clips else 0.0,
                },
                "failures": failures,
            }
        )

        best = max(clips, key=lambda c: c["interest_score"])
        ctx.log.info(
            "оценено %d из %d, отобрано %d; лучший %.2f — %s",
            len(clips), len(candidates), len(top), best["interest_score"], best["explanation"],
        )
        if failures:
            ctx.log.warning("пакетов не оценено: %d", len(failures))

    def _ask(
        self,
        ctx: StageContext,
        key: str,
        chain: list[str],
        batch: list[tuple[int, dict[str, Any]]],
        transcript: dict[str, Any],
    ) -> tuple[dict[int, dict[str, Any]], str]:
        blocks = [
            prompts.render_candidate(
                index,
                candidate,
                transcript_lines(
                    transcript,
                    candidate["start"] - CONTEXT_MARGIN,
                    candidate["end"] + CONTEXT_MARGIN,
                ),
            )
            for index, candidate in batch
        ]

        response = llm.chat(
            key,
            chain,
            {
                "messages": [
                    {"role": "system", "content": prompts.SYSTEM_PROMPT},
                    {"role": "user", "content": prompts.build_user_message(blocks)},
                ],
                "response_format": prompts.response_schema(),
                "temperature": 0.2,
            },
            timeout=ctx.config.llm.timeout_seconds,
            max_retries=ctx.config.llm.max_retries,
            provider_name=ctx.config.llm.provider,
        )

        choices = response.get("choices") or []
        content = choices[0].get("message", {}).get("content", "") if choices else ""
        known = {index for index, _ in batch}
        answered = {
            int(item["index"]): item
            for item in parse_reply(content)
            # Модель иногда придумывает номера — берём только те, что просили.
            if isinstance(item.get("index"), int) and int(item["index"]) in known
        }
        return answered, response.get("model") or chain[0]
