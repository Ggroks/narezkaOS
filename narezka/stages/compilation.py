"""Стадия compilation: длинный горизонтальный ролик.

BAZA.md §20, §21. Два режима, и это разные продукты:

- **сюжет** — связный эпизод записи, уплотнённый до нужной длины. Сюжет
  не придумывается, он уже есть: раунд, просмотр видео, дорога;
- **подборка** — лучшие моменты со всей записи, расставленные по ролям:
  зацепка, середина в хронологии, кульминация, концовка.

Это не выбор одного из двух. Можно собрать оба, только одно или ничего —
они отвечают на разные запросы и друг другу не мешают. Эпизодов тоже
берётся столько, сколько нужно: выбирать один за человека незачем, он
видел список с названиями.
"""

from __future__ import annotations

from typing import Any

from narezka.core import compilation as core
from narezka.core.artifacts import Artifact
from narezka.core.assemble import cut_piece, join_pieces, total_duration
from narezka.core import chapters as chap
from narezka.core import llm
from narezka.core.prompts import MARKS_SYSTEM_PROMPT, build_marks_message
from narezka.core.clips import load_clips
from narezka.core.media import find_source
from narezka.core.stage import Device, Stage, StageContext, StageSkipped

EPISODES_NAME = "episodes.json"
PLAN_NAME = "compilation.json"


class CompilationStage(Stage):
    name = "compilation"
    #: v1 — сюжетный эпизод и подборка лучших моментов.
    version = 1
    #: Двадцать минут видео перекодируются дольше тридцати шортсов.
    timeout_seconds = 3600
    device = Device.ANY
    optional = True
    description = "Длинная компиляция: сюжетный эпизод или подборка лучших"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        inputs = [Artifact(ctx.paths.analysis / "selection.json")]
        episodes = Artifact(ctx.paths.analysis / EPISODES_NAME)
        if episodes.exists():
            inputs.append(episodes)
        return inputs

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        artifacts = [Artifact(ctx.paths.analysis / PLAN_NAME)]
        plan = Artifact(ctx.paths.analysis / PLAN_NAME)
        if plan.exists():
            try:
                for item in plan.read_json().get("results", []):
                    artifacts.append(Artifact(ctx.paths.base / item["file"]))
            except (ValueError, KeyError, TypeError):
                pass
        return artifacts

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        cfg = ctx.config.compilation
        return {
            "story": cfg.story,
            "best": cfg.best,
            "target_minutes": cfg.target_minutes,
            "episodes": cfg.episodes,
            "width": ctx.config.output.compilation_width,
            "height": ctx.config.output.compilation_height,
        }

    def check_available(self, ctx: StageContext) -> str | None:
        cfg = ctx.config.compilation
        if not cfg.enabled:
            return "компиляция выключена"
        if not cfg.story and not cfg.best:
            return "не выбран ни сюжет, ни подборка — собирать нечего"
        return None

    def run(self, ctx: StageContext) -> None:
        cfg = ctx.config.compilation
        target = cfg.target_minutes * 60.0
        clips, source_name = load_clips(ctx.paths)
        if not clips:
            raise StageSkipped("нет отобранных моментов")

        # Заданий может быть несколько: подборка и любое число эпизодов.
        # Каждое даёт свой файл — сводить их в один было бы склейкой разных
        # рассказов, а это ровно то, чего сюжетная нарезка избегает.
        jobs: list[tuple[str, list, dict]] = []
        if cfg.best:
            pieces, plan = self._best(ctx, clips, target)
            if pieces:
                jobs.append(("compilation-best.mp4", pieces, plan))
        if cfg.story:
            jobs.extend(self._story_jobs(ctx, clips, target))

        if not jobs:
            raise StageSkipped("собирать нечего")

        ctx.log.info("заданий: %d", len(jobs))
        source = find_source(ctx.paths.source).resolve()
        results: list[dict[str, Any]] = []
        steps = sum(len(pieces) + 1 for _, pieces, _ in jobs)
        done = 0

        for name, pieces, plan in jobs:
            ctx.log.info(
                "%s: кусков %d, длительность %.1f мин",
                name, len(pieces), total_duration(pieces) / 60,
            )
            target_file = Artifact(ctx.paths.base / name)
            done = self._assemble(ctx, source, pieces, target_file, done, steps)
            length = total_duration(pieces)
            ready = plan.pop("marks", None)
            marks = ready if ready else chap.build(pieces, plan.pop("titles", None))
            plan.pop("titles", None)
            results.append({
                "file": name,
                "duration": round(length, 2),
                "pieces": [{"start": round(a, 2), "end": round(b, 2)} for a, b in pieces],
                "chapters": [c.as_dict() for c in marks],
                # Готовая строка для описания под видео: её копируют целиком,
                # и собирать её на стороне интерфейса значило бы повторять
                # правила площадок в двух местах.
                "chapters_text": chap.as_text(marks, length),
                **plan,
            })
            ctx.log.info(
                "готово: %s, %.0f МБ", name, target_file.path.stat().st_size / 1024**2
            )

        Artifact(ctx.paths.analysis / PLAN_NAME).write_json({
            "source": source_name,
            "results": results,
        })

    def _assemble(self, ctx, source, pieces, target_file, done: int, steps: int) -> int:
        """Режет куски по одному и соединяет копированием потока.

        По одному потому, что склейка одним проходом держит раскодированное
        до тех пор, пока до него не дойдёт очередь: на 28 кусках вразнобой
        это съедало всю память машины.
        """
        out = ctx.config.output
        workdir = ctx.paths.base / f"{target_file.path.stem}.parts"
        workdir.mkdir(parents=True, exist_ok=True)
        made: list[Any] = []
        try:
            for number, (start, end) in enumerate(pieces):
                done += 1
                ctx.progress(done, steps, "нарезка кусков")
                part = workdir / f"{number:03d}.mp4"
                cut_piece(
                    source, start, end, part,
                    width=out.compilation_width, height=out.compilation_height,
                    crf=out.crf, pix_fmt=out.pix_fmt, fps=out.fps,
                )
                made.append(part)

            done += 1
            ctx.progress(done, steps, "склейка")
            with target_file.reserve() as tmp:
                join_pieces(made, workdir / "list.txt", tmp)
        finally:
            # Временные куски убираются всегда: два десятка файлов по сотне
            # мегабайт не должны переживать неудачную сборку.
            for path in workdir.glob("*"):
                path.unlink(missing_ok=True)
            workdir.rmdir()
        return done

    def _mark_chapters(self, ctx: StageContext, pieces):
        """Размечает главы сюжетной нарезки по смене подтемы.

        Модель получает расшифровку в **выходном** времени и отвечает в нём
        же: пересчитывать её ответ не приходится, а именно там появлялись бы
        ошибки. Отказ модели не срывает сборку — ролик выйдет без оглавления,
        это досадно, но не сравнимо с потерей самого ролика.
        """
        artifact = Artifact(ctx.paths.transcript / "transcript.json")
        if not artifact.exists() or not ctx.config.llm.model:
            return []
        try:
            segments = artifact.read_json().get("segments", [])
        except ValueError:
            return []

        total = sum(max(0.0, b - a) for a, b in pieces)
        digest = chap.output_digest(segments, pieces)
        if not digest:
            return []

        key = llm.api_key(provider_name=ctx.config.llm.provider)
        if not key:
            return []
        try:
            response = llm.chat(
                key,
                [ctx.config.llm.model, *ctx.config.llm.fallback_models],
                {
                    "messages": [
                        {"role": "system", "content": MARKS_SYSTEM_PROMPT},
                        {"role": "user", "content": build_marks_message(digest, total)},
                    ],
                    "temperature": 0.3,
                },
                timeout=ctx.config.llm.timeout_seconds,
                provider_name=ctx.config.llm.provider,
            )
            content = response["choices"][0]["message"]["content"]
        except (llm.LlmError, ValueError, KeyError, IndexError) as exc:
            ctx.log.warning("главы не размечены — оглавления не будет: %s", exc)
            return []

        marks = chap.parse_marks(content, total)
        ctx.log.info("глав размечено: %d", len(marks))
        return marks

    @staticmethod
    def _titles(ctx: StageContext) -> dict[int, str]:
        """Заголовки клипов, если стадия текстов выполнялась."""
        artifact = Artifact(ctx.paths.analysis / "publish.json")
        if not artifact.exists():
            return {}
        try:
            return {c["index"]: c.get("title", "") for c in artifact.read_json().get("clips", [])}
        except (ValueError, KeyError, TypeError):
            return {}

    def _story_jobs(self, ctx: StageContext, clips, target: float):
        """Задания на сюжетные ролики — по одному на выбранный эпизод."""
        artifact = Artifact(ctx.paths.analysis / EPISODES_NAME)
        if not artifact.exists():
            ctx.log.warning(
                "эпизоды не размечены — сюжетных роликов не будет; "
                "запустите поиск эпизодов или включите подборку лучших"
            )
            return []
        episodes = artifact.read_json().get("episodes", [])
        if not episodes:
            ctx.log.warning("связных эпизодов в записи не нашлось")
            return []

        chosen = ctx.config.compilation.episodes
        if chosen is None:
            # Без явного выбора берётся самый цельный и достаточно длинный:
            # короткий цельный не растянуть до двадцати минут, а длинный
            # бессвязный не станет рассказом.
            picked = [max(range(len(episodes)), key=lambda i: episodes[i]["coherence"] * episodes[i]["duration"])]
        else:
            picked = [i for i in chosen if 0 <= i < len(episodes)]
            skipped = [i for i in chosen if not 0 <= i < len(episodes)]
            if skipped:
                ctx.log.warning("нет эпизодов с номерами %s — пропущены", skipped)

        jobs = []
        for index in picked:
            episode = episodes[index]
            ctx.log.info(
                "эпизод %d «%s», %.0f–%.0f мин, цельность %.2f",
                index, episode["title"], episode["start"] / 60,
                episode["end"] / 60, episode["coherence"],
            )
            keep = [
                (c["start"], c["end"]) for c in clips
                if c["start"] < episode["end"] and c["end"] > episode["start"]
            ]
            pieces = core.condense(episode["start"], episode["end"], keep, target)
            if pieces:
                jobs.append((
                    f"compilation-story-{index:02d}.mp4",
                    pieces,
                    {
                        "episode": episode,
                        "episode_index": index,
                        "highlights_inside": len(keep),
                        # Главы размечаются по смене подтемы внутри занятия,
                        # а не по кускам: уплотнение даёт два-три куска, и
                        # оглавление из одной строки на восемь минут им и не
                        # является. Проверено на живой нарезке.
                        "marks": self._mark_chapters(ctx, pieces),
                    },
                ))
        return jobs

    def _best(self, ctx: StageContext, clips, target: float):
        """Подборка лучших: моменты со всей записи, расставленные по ролям."""
        result = core.arrange(clips, target)
        pieces = [(p.clip["start"], p.clip["end"]) for p in result.parts]
        # Заголовки уже написаны стадией metadata — брать их заново значило бы
        # платить за то, что лежит рядом.
        written = self._titles(ctx)
        titles = {
            number: written.get(part.clip.get("index"), "")
            for number, part in enumerate(result.parts)
        }
        for part in result.parts:
            ctx.log.info(
                "  %-8s #%s оценка %s",
                part.role, part.clip.get("index"), part.clip.get("interest_score"),
            )
        return pieces, {"arrangement": result.as_dict(), "titles": titles}
