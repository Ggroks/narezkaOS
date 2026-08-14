"""Тесты выбора источника клипов (BAZA.md §11, §43).

Смысл: сборка обязана брать последний доступный слой воронки. Если отбор
моделью выполнен — ролики режутся по уточнённым границам, иначе по сырым
кандидатам. Логика живёт в одном месте, чтобы субтитры и видео не разошлись.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from narezka.core.clips import load_clips


class Paths:
    def __init__(self, analysis: Path) -> None:
        self.analysis = analysis


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    return Paths(analysis)


def write(paths: Paths, name: str, payload: dict) -> None:
    (paths.analysis / name).write_text(json.dumps(payload), encoding="utf-8")


CANDIDATES = {"candidates": [{"start": 10.0, "end": 40.0}, {"start": 100.0, "end": 130.0}]}
SELECTION = {
    "clips": [
        {"index": 1, "start": 105.0, "end": 128.0, "interest_score": 0.8, "rank": 1},
    ]
}


def test_candidates_are_used_when_there_is_no_selection(paths) -> None:
    """Без ключа стадия llm_select пропускается — пайплайн обязан работать."""
    write(paths, "candidates.json", CANDIDATES)
    clips, source = load_clips(paths)
    assert source == "candidates"
    assert [c["index"] for c in clips] == [0, 1]


def test_selection_wins_over_candidates(paths) -> None:
    write(paths, "candidates.json", CANDIDATES)
    write(paths, "selection.json", SELECTION)
    clips, source = load_clips(paths)
    assert source == "selection"
    assert len(clips) == 1
    assert clips[0]["interest_score"] == 0.8


def test_refined_bounds_are_what_gets_cut(paths) -> None:
    """Иначе модель уточняет границы впустую: в ролик попадает старое окно."""
    write(paths, "candidates.json", CANDIDATES)
    write(paths, "selection.json", SELECTION)
    clips, _ = load_clips(paths)
    assert (clips[0]["start"], clips[0]["end"]) == (105.0, 128.0)


def test_index_stays_the_candidate_index(paths) -> None:
    """Сквозной номер не должен зависеть от того, отработала модель или нет:
    на него ссылается разметка человеком и имена файлов (§35)."""
    write(paths, "candidates.json", CANDIDATES)
    write(paths, "selection.json", SELECTION)
    clips, _ = load_clips(paths)
    assert clips[0]["index"] == 1


def test_empty_selection_falls_back_to_candidates(paths) -> None:
    """Модель может не оценить ни одного пакета — не повод остаться без роликов."""
    write(paths, "candidates.json", CANDIDATES)
    write(paths, "selection.json", {"clips": []})
    clips, source = load_clips(paths)
    assert source == "candidates"
    assert len(clips) == 2


def test_broken_selection_falls_back_instead_of_crashing(paths) -> None:
    write(paths, "candidates.json", CANDIDATES)
    (paths.analysis / "selection.json").write_text("{битый", encoding="utf-8")
    clips, source = load_clips(paths)
    assert source == "candidates"
    assert len(clips) == 2


def test_nothing_at_all_gives_empty_list(paths) -> None:
    clips, source = load_clips(paths)
    assert clips == []
    assert source == "none"


def test_duration_is_computed(paths) -> None:
    write(paths, "candidates.json", CANDIDATES)
    clips, _ = load_clips(paths)
    assert clips[0]["duration"] == 30.0
