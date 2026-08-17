"""Стадия episodes: разметка записи на связные занятия.

BAZA.md §20. Готовит выбор для сюжетной компиляции: не «вот лучшие моменты»,
а «вот три занятия, из которых можно собрать рассказ» — раунд, просмотр
видео, дорога, разбор события.

Стадия необязательна: для подборки лучших моментов она не нужна, а стоит
запросов к модели.
"""

from __future__ import annotations

from typing import Any

from narezka.core import episodes as core
from narezka.core import llm
from narezka.core.artifacts import Artifact
from narezka.core.prompts import EPISODES_SYSTEM_PROMPT, build_episodes_message
from narezka.core.stage import Device, Stage, StageContext, StageSkipped

TRANSCRIPT_NAME = "transcript.json"
EPISODES_NAME = "episodes.json"


class EpisodesStage(Stage):
    name = "episodes"
    #: v1 — разметка окон модели с перекрытием и слиянием стыков.
    version = 1
    device = Device.ANY
    optional = True
    description = "Эпизоды: связные занятия для сюжетной компиляции"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.transcript / TRANSCRIPT_NAME)]

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        return [Artifact(ctx.paths.analysis / EPISODES_NAME)]

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        return {
            "enabled": ctx.config.compilation.find_episodes,
            "model": ctx.config.llm.model,
            "chunk_seconds": core.CHUNK_SECONDS,
        }

    def check_available(self, ctx: StageContext) -> str | None:
        if not ctx.config.compilation.find_episodes:
            return "поиск эпизодов выключен"
        provider = llm.provider(ctx.config.llm.provider)
        if not llm.api_key(provider_name=ctx.config.llm.provider):
            return f"нет ключа {provider.key_env}"
        if not ctx.config.llm.model:
            return "в конфиге не выбрана модель"
        return None

    def run(self, ctx: StageContext) -> None:
        data = Artifact(ctx.paths.transcript / TRANSCRIPT_NAME).read_json()
        segments = data.get("segments", [])
        if not segments:
            raise StageSkipped("в расшифровке нет речи")

        duration = float(data.get("duration_seconds") or segments[-1].get("end", 0.0))
        windows = core.chunks(duration)
        ctx.log.info("окон разметки %d по %.0f мин", len(windows), core.CHUNK_SECONDS / 60)

        key = llm.api_key(provider_name=ctx.config.llm.provider)
        chain = [ctx.config.llm.model, *ctx.config.llm.fallback_models]

        def ask(window):
            start, end = window
            digest = core.transcript_digest(segments, start, end)
            if not digest:
                return {}, "пусто"
            response = llm.chat(
                key,
                chain,
                {
                    "messages": [
                        {"role": "system", "content": EPISODES_SYSTEM_PROMPT},
                        {"role": "user", "content": build_episodes_message(digest, start, end)},
                    ],
                    "temperature": 0.2,
                },
                timeout=ctx.config.llm.timeout_seconds,
                max_retries=ctx.config.llm.max_retries,
                provider_name=ctx.config.llm.provider,
            )
            choices = response.get("choices") or []
            answer = choices[0].get("message", {}).get("content", "") if choices else ""
            model = response.get("model", chain[0])
            dropped: list[str] = []
            parsed = core.parse(answer, start, end, rejected=dropped)
            if dropped:
                ctx.log.info(
                    "окно %.0f–%.0f мин: отсеяно %d по длине, например %s",
                    start / 60, end / 60, len(dropped), "; ".join(dropped[:3]),
                )
            if not parsed:
                # Пустой разбор бывает по двум причинам: модель честно не
                # нашла занятий или ответила не в том виде. Различить их
                # можно только увидев ответ, поэтому его начало попадает
                # в журнал — иначе стадия молчит и чинить нечего.
                ctx.log.warning(
                    "окно %.0f–%.0f мин: ничего не разобрано, ответ начинается так: %s",
                    start / 60, end / 60, answer[:200].replace("\n", " ") or "(пусто)",
                )
            return {id(window): parsed}, model

        answers, models_used, failures = llm.process_batches(
            windows, ask, ctx.log, label="окно",
            on_progress=lambda done, _t, note: ctx.progress(done, len(windows), note),
        )

        found = [ep for value in answers.values() if isinstance(value, list) for ep in value]
        merged = core.merge(found)
        ctx.log.info(
            "найдено %d эпизодов, после слияния стыков %d", len(found), len(merged)
        )
        for episode in merged:
            ctx.log.info(
                "  %.0f–%.0f мин · %s (цельность %.2f)",
                episode.start / 60, episode.end / 60, episode.title, episode.coherence,
            )

        if not merged:
            raise StageSkipped(
                "связных занятий не найдено — для подборки лучших моментов это не помеха"
            )

        Artifact(ctx.paths.analysis / EPISODES_NAME).write_json({
            "model": ", ".join(sorted(models_used)),
            "windows": len(windows),
            "failures": failures[:5],
            "episodes": [episode.as_dict() for episode in merged],
        })
