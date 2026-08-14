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
"""

from __future__ import annotations

from typing import Any

from narezka.core.artifacts import Artifact

CANDIDATES_NAME = "candidates.json"
SELECTION_NAME = "selection.json"


def load_clips(paths) -> tuple[list[dict[str, Any]], str]:
    """Клипы для сборки и то, откуда они взяты.

    Возвращает пустой список, если нет ни отбора, ни кандидатов — решение,
    что с этим делать, принимает вызывающая стадия.
    """
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
