"""Что именно собирать в ролики.

BAZA.md §11, §44. Воронка даёт два слоя результата, и сборка обязана брать
последний доступный:

1. `selection.json` — отбор моделью: уточнённые границы, оценки, top-N;
2. `candidates.json` — сырые окна от дешёвых сигналов.

Стадия `llm_select` опциональна (§43): без ключа она пропускается, и тогда
ролики собираются по кандидатам. Поэтому выбор источника решается здесь,
а не дублируется в каждой стадии сборки — иначе одна из них однажды
разойдётся с другой, и субтитры окажутся от одного клипа, а видео от другого.

Сквозной номер клипа — **индекс кандидата**, а не позиция в отобранном
списке. Он не меняется от того, отработала модель или нет, поэтому на него
можно ссылаться из разметки человеком (§35) и из имён файлов.

**Слово человека последнее.** Поверх обоих слоёв ложится разметка из обзора
моментов: отклонённый момент в сборку не идёт, а подвинутые вручную границы
берутся вместо расчётных. Иначе «не годится» в интерфейсе не значило бы
ничего — момент всё равно оказался бы в готовых роликах.
"""

from __future__ import annotations

from typing import Any

from narezka.core import review as review_module
from narezka.core.artifacts import Artifact

CANDIDATES_NAME = "candidates.json"
SELECTION_NAME = "selection.json"


def load_clips(paths, *, apply_review: bool = True) -> tuple[list[dict[str, Any]], str]:
    """Клипы для сборки и то, откуда они взяты.

    Возвращает пустой список, если нет ни отбора, ни кандидатов — решение,
    что с этим делать, принимает вызывающая стадия.

    `apply_review=False` отдаёт всё как есть, вместе с отклонённым: обзору
    моментов и полосе записи нужно показывать и то, что человек отбросил, —
    иначе решение нельзя ни увидеть, ни отменить.
    """
    clips, source = _from_artifacts(paths)
    if apply_review:
        clips = _apply_review(paths, clips, source)
    return clips, source


def clip_inputs(paths) -> list[Artifact]:
    """Артефакты, от которых зависит состав клипов, — для ключа кэша.

    Разметка человека стоит здесь наравне с отбором: без неё «не годится»,
    поставленное после сборки, не пересобрало бы ролики — стадия молча
    отдала бы прежние. Ровно так однажды разъехались тексты и границы,
    когда `metadata` не объявляла клипы своим входом.
    """
    selection = Artifact(paths.analysis / SELECTION_NAME)
    inputs = [selection if selection.exists() else Artifact(paths.analysis / CANDIDATES_NAME)]
    decisions = Artifact(paths.review)
    if decisions.exists():
        inputs.append(decisions)
    return inputs


def _from_artifacts(paths) -> tuple[list[dict[str, Any]], str]:
    selection = Artifact(paths.analysis / SELECTION_NAME)
    if selection.exists():
        try:
            clips = selection.read_json().get("clips", [])
        except ValueError:
            clips = []
        if clips:
            return [_normalize(clip, index=clip.get("index")) for clip in clips], "selection"

    candidates = Artifact(paths.analysis / CANDIDATES_NAME)
    if candidates.exists():
        try:
            raw = candidates.read_json().get("candidates", [])
        except ValueError:
            raw = []
        return [_normalize(clip, index=index) for index, clip in enumerate(raw)], "candidates"

    return [], "none"


def _apply_review(paths, clips: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """Слово человека поверх отбора — в обе стороны.

    «Не годится» убирает момент из сборки, «годится» — возвращает тот, что
    модель отбросила. Без второго выбор был односторонним: убрать лишнее
    можно, а вернуть зря выброшенное нельзя, и «годится» оставалось отметкой
    ни на что не влияющей.

    Неразмеченный момент остаётся: отсутствие решения — это «ещё не смотрел»,
    а не «не нужен». Требовать одобрения на каждый значило бы заставлять
    размечать тридцать штук ради одной сборки.

    Границы берутся только те, что человек действительно двигал: в записи
    решения лежат границы кандидата на момент нажатия, и подставлять их
    поверх уточнённых моделью значило бы откатывать уточнение.
    """
    artifact = Artifact(paths.review)
    if not artifact.exists():
        return clips
    try:
        decisions = review_module.by_index(artifact.read_json())
    except ValueError:
        return clips
    if not decisions:
        return clips

    kept = []
    for clip in clips:
        entry = decisions.get(clip.get("index"))
        if entry is None:
            kept.append(clip)
            continue
        if entry.get("verdict") == "reject":
            continue
        if review_module.moved(entry):
            start, end = float(entry["start"]), float(entry["end"])
            clip = {**clip, "start": start, "end": end, "duration": round(end - start, 3)}
        kept.append(clip)

    if source == "selection":
        kept.extend(_rescued(paths, decisions, {clip.get("index") for clip in clips}))
        # По времени, а не по оценке: ролики идут в том порядке, в каком шли
        # в записи, и возвращённый момент обязан встать на своё место.
        kept.sort(key=lambda clip: clip["start"])
    return kept


def _rescued(paths, decisions: dict, selected: set) -> list[dict[str, Any]]:
    """Моменты, которые модель не выбрала, а человек отметил «годится».

    Оценки у них нет и взяться ей неоткуда: модель их не разбирала. Ставить
    им ноль было бы неправдой — `interest_score` остаётся None, «не измерено»
    (§54), а `rescued` говорит, откуда момент взялся.
    """
    wanted = [
        index for index, entry in decisions.items()
        if entry.get("verdict") == "accept" and index not in selected
    ]
    if not wanted:
        return []

    artifact = Artifact(paths.analysis / CANDIDATES_NAME)
    if not artifact.exists():
        return []
    try:
        candidates = artifact.read_json().get("candidates", [])
    except ValueError:
        return []

    rescued = []
    for index in sorted(wanted):
        if not 0 <= index < len(candidates):
            continue
        entry = decisions[index]
        clip = _normalize(candidates[index], index=index)
        # Границы человека здесь берутся всегда, а не только подвинутые:
        # уточнять их было некому — модель этот момент не разбирала.
        start, end = float(entry["start"]), float(entry["end"])
        rescued.append({
            **clip,
            "start": start,
            "end": end,
            "duration": round(end - start, 3),
            "interest_score": None,
            "rank": None,
            "rescued": True,
            "explanation": "Момент вернул человек: модель его не выбрала",
        })
    return rescued


def review_summary(paths) -> dict[str, int]:
    """Сколько моментов человек отклонил и сколько поправил руками.

    Считается по самой разметке, а не по отобранному списку: отклонённых
    в нём уже нет, и по нему их не сосчитать.
    """
    artifact = Artifact(paths.review)
    if not artifact.exists():
        return {"rejected": 0, "edited": 0}
    try:
        decisions = review_module.by_index(artifact.read_json())
    except ValueError:
        return {"rejected": 0, "edited": 0}
    return {
        "rejected": sum(1 for entry in decisions.values() if entry.get("verdict") == "reject"),
        "edited": sum(1 for entry in decisions.values() if review_module.moved(entry)),
    }


def _normalize(clip: dict[str, Any], index: Any) -> dict[str, Any]:
    start = float(clip["start"])
    end = float(clip["end"])
    return {
        **clip,
        "index": int(index) if isinstance(index, int) else 0,
        "start": start,
        "end": end,
        "duration": round(end - start, 3),
    }


def describe_source(source: str, count: int) -> str:
    if source == "selection":
        return f"клипов {count} — отобраны моделью, границы уточнены"
    if source == "candidates":
        return f"клипов {count} — кандидаты от дешёвых сигналов, отбор моделью не выполнялся"
    return "клипов нет"
