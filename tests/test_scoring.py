"""Тесты оценки клипа (BAZA.md §12, §38, §54).

Главное правило схемы: `interest_score` — производная от факторов по весам
из конфига. Если оценку начнёт возвращать модель, веса перестанут на что-либо
влиять, а отладить результат будет нечем.
"""

from __future__ import annotations

import pytest

from narezka.core.scoring import (
    MEASURED_FACTOR,
    TEXT_FACTORS,
    UNMEASURED_PENALTIES,
    VISUAL_FACTOR,
    audio_factor,
    build_clip,
    interest_score,
    normalized_weights,
    select_top,
)

WEIGHTS = {
    "semantic": 0.25, "emotion": 0.20, "context": 0.20,
    "completeness": 0.15, "audio": 0.10, "novelty": 0.05, "visual": 0.05,
}


def candidate(start=10.0, end=40.0, loudness_z=2.7):
    return {"start": start, "end": end, "signals": {"loudness_z": loudness_z}}


def verdict(**overrides):
    base = {
        "start": 12.0,
        "end": 38.0,
        "clip_type": "emotional_peak",
        "explanation": "смешной момент с развязкой",
        "factors": dict.fromkeys(TEXT_FACTORS, 0.8),
        "penalties": {"unresolved_ending": 0.0},
    }
    base.update(overrides)
    return base


# --- веса ------------------------------------------------------------------


def test_weights_are_renormalized_over_measured_factors() -> None:
    """Неизмеренный фактор не должен занижать оценку.

    Визуальный появится только на этапе 4; до тех пор его вес обязан
    распределиться между остальными, иначе клипы штрафуются за то,
    что часть системы ещё не написана.
    """
    share = normalized_weights(WEIGHTS, ["semantic", "emotion"])
    assert sum(share.values()) == pytest.approx(1.0)
    assert share["semantic"] > share["emotion"]


def test_zero_weights_fall_back_to_equal_shares() -> None:
    share = normalized_weights({}, ["a", "b"])
    assert share == {"a": 0.5, "b": 0.5}


def test_all_factors_high_gives_high_score() -> None:
    assert interest_score(dict.fromkeys(TEXT_FACTORS, 1.0), {}, WEIGHTS) == 1.0


def test_no_measured_factors_is_zero_not_a_crash() -> None:
    assert interest_score({"semantic": None}, {}, WEIGHTS) == 0.0


def test_out_of_range_values_are_clamped() -> None:
    """Модель может вернуть 5 или -1 — шкала должна остаться 0–1 (§12)."""
    assert interest_score({"semantic": 42}, {}, WEIGHTS) == 1.0
    assert interest_score({"semantic": -3}, {}, WEIGHTS) == 0.0


# --- штрафы ----------------------------------------------------------------


def test_penalty_lowers_but_does_not_zero_the_score() -> None:
    """Музыка делает клип хуже, но не превращает в мусор (§59)."""
    clean = interest_score(dict.fromkeys(TEXT_FACTORS, 1.0), {}, WEIGHTS)
    punished = interest_score(dict.fromkeys(TEXT_FACTORS, 1.0), {"x": 1.0}, WEIGHTS)
    assert 0 < punished < clean


def test_unmeasured_penalty_is_ignored_not_counted_as_zero() -> None:
    """None — «не проверяли», и это не то же самое, что «проверили, нет»."""
    with_none = interest_score({"semantic": 1.0}, {"music_present": None}, WEIGHTS)
    without = interest_score({"semantic": 1.0}, {}, WEIGHTS)
    assert with_none == without


# --- измеренный звук -------------------------------------------------------


def test_audio_comes_from_measurement() -> None:
    """Модель читает текст и не слышит записи: на первом прогоне она ставила
    сюда ноль всем клипам подряд. Значение берётся из замера громкости."""
    assert audio_factor({"loudness_z": 3.0}) == 1.0
    assert audio_factor({"loudness_z": 1.5}) == 0.5


def test_audio_is_none_without_measurement() -> None:
    assert audio_factor({}) is None
    assert audio_factor(None) is None
    assert audio_factor({"loudness_z": "громко"}) is None


def test_build_clip_ignores_model_opinion_about_audio() -> None:
    clip = build_clip(
        video_id="v1", index=0,
        candidate=candidate(loudness_z=3.0),
        verdict=verdict(factors={**dict.fromkeys(TEXT_FACTORS, 0.8), "audio": 0.0}),
        weights=WEIGHTS, schema_version=2, model="m",
    )
    assert clip["factors"][MEASURED_FACTOR] == 1.0


# --- сборка клипа ----------------------------------------------------------


def test_refined_bounds_are_used() -> None:
    """§14: оценка считается после уточнения границ, значит и границы
    в записи должны быть уточнённые."""
    clip = build_clip(
        video_id="v1", index=0, candidate=candidate(10.0, 40.0),
        verdict=verdict(start=12.0, end=38.0),
        weights=WEIGHTS, schema_version=2, model="m",
    )
    assert (clip["start"], clip["end"]) == (12.0, 38.0)
    assert clip["original"] == {"start": 10.0, "end": 40.0}
    assert clip["duration"] == 26.0


def test_backwards_bounds_fall_back_to_the_candidate() -> None:
    """Модель иногда путает начало и конец — клип не должен стать пустым."""
    clip = build_clip(
        video_id="v1", index=0, candidate=candidate(10.0, 40.0),
        verdict=verdict(start=50.0, end=20.0),
        weights=WEIGHTS, schema_version=2, model="m",
    )
    assert (clip["start"], clip["end"]) == (10.0, 40.0)


def test_visual_and_music_stay_unmeasured() -> None:
    """§54: выдуманное число, неотличимое от измеренного, — самообман."""
    clip = build_clip(
        video_id="v1", index=0, candidate=candidate(),
        verdict=verdict(), weights=WEIGHTS, schema_version=2, model="m",
    )
    assert clip["factors"][VISUAL_FACTOR] is None
    for name in UNMEASURED_PENALTIES:
        assert clip["penalties"][name] is None


def test_clip_id_is_stable_and_readable() -> None:
    clip = build_clip(
        video_id="abc123", index=7, candidate=candidate(),
        verdict=verdict(), weights=WEIGHTS, schema_version=2, model="m",
    )
    assert clip["clip_id"] == "abc123_c07"


def test_explanation_is_required_by_schema() -> None:
    """§12: без объяснения оценка бесполезна для отладки."""
    clip = build_clip(
        video_id="v1", index=0, candidate=candidate(),
        verdict=verdict(explanation="  смешно  "),
        weights=WEIGHTS, schema_version=2, model="m",
    )
    assert clip["explanation"] == "смешно"


# --- отбор лучших ----------------------------------------------------------


def make_clip(index: int, score: float, start: float) -> dict:
    return {"index": index, "interest_score": score, "start": start}


def test_top_n_keeps_the_best() -> None:
    clips = [make_clip(0, 0.2, 10), make_clip(1, 0.9, 20), make_clip(2, 0.5, 30)]
    top = select_top(clips, limit=2)
    assert {c["index"] for c in top} == {1, 2}


def test_output_is_ordered_by_time_not_by_score() -> None:
    """Смотреть нарезку удобнее в порядке записи; ранг записан отдельно."""
    clips = [make_clip(0, 0.5, 300), make_clip(1, 0.9, 100)]
    top = select_top(clips, limit=2)
    assert [c["start"] for c in top] == [100, 300]
    assert [c["rank"] for c in top] == [1, 2]


def test_min_score_filters_weak_clips() -> None:
    clips = [make_clip(0, 0.1, 10), make_clip(1, 0.8, 20)]
    assert len(select_top(clips, limit=10, min_score=0.5)) == 1


def test_empty_input_gives_empty_output() -> None:
    assert select_top([], limit=5) == []


# --- границы против ограничений площадки -----------------------------------


def test_too_short_refinement_is_extended() -> None:
    """Реальный случай с записи стрима: модель ужала клип с 62 секунд до 6.

    Она уточняет границы по смыслу и об ограничениях площадки не знает —
    шестисекундный ролик публиковать некуда. Проверять обязан код.
    """
    from narezka.core.scoring import fit_duration

    start, end = fit_duration(
        100.0, 106.2, min_duration=15, max_duration=90,
        limit_start=90.0, limit_end=152.0,
    )
    assert end - start == 15.0
    # Начало сохранено: §14 требует смысловой завязки, её модель выбирала.
    assert start == 100.0


def test_too_long_refinement_is_trimmed() -> None:
    from narezka.core.scoring import fit_duration

    start, end = fit_duration(
        10.0, 200.0, min_duration=15, max_duration=90,
        limit_start=10.0, limit_end=200.0,
    )
    assert end - start == 90.0


def test_extension_stops_at_the_candidate_edge() -> None:
    """Растягивать за пределы кандидата нельзя: там материал, который
    дешёвые сигналы не сочли интересным."""
    from narezka.core.scoring import fit_duration

    start, end = fit_duration(
        140.0, 145.0, min_duration=15, max_duration=90,
        limit_start=100.0, limit_end=150.0,
    )
    assert end == 150.0
    assert end - start == 15.0


def test_candidate_shorter_than_minimum_is_left_alone() -> None:
    """Растягивать не из чего — отдаём кандидата как есть, а не выдумываем."""
    from narezka.core.scoring import fit_duration

    assert fit_duration(
        10.0, 12.0, min_duration=15, max_duration=90,
        limit_start=10.0, limit_end=20.0,
    ) == (10.0, 20.0)


def test_build_clip_applies_the_limits() -> None:
    clip = build_clip(
        video_id="v1", index=0,
        candidate=candidate(100.0, 200.0),
        verdict=verdict(start=120.0, end=126.0),
        weights=WEIGHTS, schema_version=2, model="m",
        min_duration=15, max_duration=90,
    )
    assert clip["duration"] == 15.0


def test_music_penalty_is_none_without_measurement():
    """Без разбора звука штраф за музыку неизвестен, а не равен нулю.

    Ноль читался бы как «проверено, музыки нет» — и потому опаснее пропуска.
    """
    clip = build_clip(
        video_id="v", index=0,
        candidate={"start": 0.0, "end": 30.0, "signals": {}},
        verdict={"factors": {"semantic": 0.8}}, weights={"semantic": 1.0},
        schema_version=1, model="test",
    )
    assert clip["penalties"]["music_present"] is None


def test_measured_music_lowers_the_score():
    """Измеренная музыка снижает оценку: ролик с ней ловит Content ID."""
    common = dict(
        video_id="v", index=0,
        candidate={"start": 0.0, "end": 30.0, "signals": {}},
        verdict={"factors": {"semantic": 0.9}}, weights={"semantic": 1.0},
        schema_version=1, model="test",
    )
    clean = build_clip(**common)
    musical = build_clip(**common, measured_penalties={"music_present": 0.9})

    assert musical["penalties"]["music_present"] == 0.9
    assert musical["interest_score"] < clean["interest_score"]
