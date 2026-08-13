"""Тесты геометрии вертикального кадра (BAZA.md §61).

Числа взяты из реальной фикстуры gubkabobtest: 1024x576 в кадр 1080x1920.
Именно на ней стало видно, что без обрезки содержимое занимает треть кадра.
"""

from __future__ import annotations

import pytest

from narezka.core.framing import (
    Framing,
    build_filter,
    describe,
    fill_side_crop,
    plan_frame,
    resolve_side_crop,
)

SRC = (1024, 576)
OUT = (1080, 1920)


def plan(preset: str = "balanced", **kwargs):
    return plan_frame(*SRC, *OUT, Framing(preset=preset, **kwargs))


# --- связь обрезки и высоты содержимого ------------------------------------


def test_no_crop_leaves_content_small() -> None:
    """Исходное поведение: ничего не теряем, но содержимое занимает треть."""
    result = plan("full")
    assert result.crop_w == SRC[0], "при full ничего не должно отрезаться"
    assert result.content_share == pytest.approx(0.32, abs=0.01)
    assert not result.full_bleed


def test_more_crop_means_taller_content() -> None:
    """Ключевая зависимость: обрезка по бокам и высота содержимого — одно и то же."""
    shares = [plan(name).content_share for name in ("full", "balanced", "focus", "fill")]
    assert shares == sorted(shares), f"доля должна расти вместе с обрезкой: {shares}"
    assert shares[-1] == 1.0


def test_fill_fills_the_frame_exactly() -> None:
    result = plan("fill")
    assert result.full_bleed
    assert result.scaled_h == OUT[1]
    assert result.content_share == 1.0


def test_fill_crop_matches_aspect_ratio() -> None:
    # 1080*576/1920 = 324 из 1024 → отрезается 68.4%
    assert fill_side_crop(*SRC, *OUT) == pytest.approx(0.6836, abs=0.001)


def test_vertical_source_needs_no_crop() -> None:
    """Исходник уже вертикальный — обрезать нечего даже в режиме fill."""
    result = plan_frame(1080, 1920, *OUT, Framing(preset="fill"))
    assert result.lost_share == 0.0
    assert result.full_bleed


def test_source_narrower_than_target_ratio() -> None:
    """Портретный, но не 9:16 — содержимое всё равно не должно вылезать за кадр."""
    result = plan_frame(1080, 1350, *OUT, Framing(preset="fill"))
    assert result.scaled_h <= OUT[1]


# --- custom и границы ------------------------------------------------------


def test_custom_uses_its_own_value() -> None:
    assert plan("custom", side_crop=0.4).lost_share == pytest.approx(0.4)


@pytest.mark.parametrize("value", [-1.0, 0.0, 0.5, 0.99, 5.0])
def test_side_crop_is_clamped(value: float) -> None:
    """Отрезать весь кадр нельзя — иначе делить на ноль при вписывании."""
    resolved = resolve_side_crop(Framing(preset="custom", side_crop=value), *SRC, *OUT)
    assert 0.0 <= resolved <= 0.95


def test_unknown_preset_falls_back_without_crash() -> None:
    resolved = resolve_side_crop(Framing(preset="какой-то"), *SRC, *OUT)
    assert resolved == 0.0


@pytest.mark.parametrize("preset", ["full", "balanced", "focus", "fill"])
def test_dimensions_are_even(preset: str) -> None:
    """h264 с yuv420p не принимает нечётные стороны — иначе рендер падает."""
    result = plan(preset)
    for value in (result.crop_w, result.crop_h, result.crop_x, result.crop_y,
                  result.scaled_w, result.scaled_h):
        assert value % 2 == 0, f"{preset}: нечётный размер {value}"


def test_crop_stays_inside_source() -> None:
    for anchor in ("center", "left", "right"):
        result = plan("focus", anchor=anchor)
        assert result.crop_x >= 0
        assert result.crop_x + result.crop_w <= SRC[0]
        assert result.crop_y + result.crop_h <= SRC[1]


# --- точка привязки --------------------------------------------------------


def test_anchor_moves_the_window() -> None:
    left = plan("focus", anchor="left")
    center = plan("focus", anchor="center")
    right = plan("focus", anchor="right")
    assert left.crop_x == 0
    assert left.crop_x < center.crop_x < right.crop_x
    assert right.crop_x + right.crop_w == SRC[0]
    # Размер окна от привязки не зависит — меняется только положение.
    assert left.crop_w == center.crop_w == right.crop_w


# --- строка фильтра --------------------------------------------------------


def test_filter_has_blur_by_default() -> None:
    chain = build_filter(plan("balanced"), Framing(), *OUT, "00.ass")
    assert "gblur=sigma=28" in chain
    assert "overlay=" in chain
    assert "subtitles=00.ass" in chain


def test_blur_can_be_turned_off() -> None:
    """sigma=0 — подложка остаётся, но резкая: размытие не считается вовсе."""
    framing = Framing(blur_sigma=0)
    chain = build_filter(plan_frame(*SRC, *OUT, framing), framing, *OUT, "00.ass")
    assert "gblur" not in chain
    assert "[bg]" in chain


def test_solid_background_instead_of_blur() -> None:
    """Полосы делает pad, а не отдельный источник color.

    Найдено на предпросмотре: источник color живёт по своим меткам времени,
    и overlay успевал выдать кадр раньше, чем подъезжал кадр видео — в кадр
    попадала одна заливка. У pad второго входа нет, синхронизировать нечего.
    """
    framing = Framing(background="color", color="0x000000")
    plan = plan_frame(*SRC, *OUT, framing)
    chain = build_filter(plan, framing, *OUT, "00.ass")
    assert f"pad={OUT[0]}:{OUT[1]}:0:{plan.offset_y}:0x000000" in chain
    assert "overlay" not in chain
    assert "gblur" not in chain


def test_full_bleed_skips_the_backdrop() -> None:
    """Подложку под заполненным кадром не видно — считать её впустую."""
    framing = Framing(preset="fill")
    chain = build_filter(plan_frame(*SRC, *OUT, framing), framing, *OUT, "00.ass")
    assert "gblur" not in chain
    assert "overlay" not in chain
    assert "[bg]" not in chain
    assert "subtitles=00.ass" in chain


def test_filter_positions_content_by_plan() -> None:
    result = plan("balanced")
    chain = build_filter(result, Framing(), *OUT, "00.ass")
    assert f"overlay=(W-w)/2:{result.offset_y}" in chain
    assert f"crop={result.crop_w}:{result.crop_h}:{result.crop_x}:{result.crop_y}" in chain


def test_sigma_is_not_written_in_scientific_notation() -> None:
    """ffmpeg не поймёт «1e-05», а форматирование %g может его выдать."""
    framing = Framing(blur_sigma=0.5)
    chain = build_filter(plan_frame(*SRC, *OUT, framing), framing, *OUT, "00.ass")
    assert "gblur=sigma=0.5" in chain


@pytest.mark.parametrize("preset", ["full", "balanced", "focus", "fill"])
@pytest.mark.parametrize("background", ["blur", "color"])
def test_preview_chain_differs_only_by_subtitles(preset: str, background: str) -> None:
    """Предпросмотр должен показывать ту же геометрию, что и рендер.

    Иначе пользователь настраивает по картинке, которая врёт.
    """
    framing = Framing(preset=preset, background=background)
    result = plan_frame(*SRC, *OUT, framing)
    with_subs = build_filter(result, framing, *OUT, "00.ass")
    preview = build_filter(result, framing, *OUT)

    assert preview.endswith("[v]")
    assert "subtitles" not in preview
    # Геометрическая часть совпадает до места, где начинаются субтитры.
    assert with_subs.startswith(preview[: -len("[v]")])


@pytest.mark.parametrize(
    "framing",
    [
        Framing(preset="balanced"),
        Framing(preset="balanced", blur_sigma=0),
        Framing(preset="balanced", background="color"),
        Framing(preset="fill"),
    ],
)
def test_every_stream_comes_from_the_input(framing: Framing) -> None:
    """Ни одной ветки из независимого источника — только из [0:v].

    Источник, живущий по собственным меткам времени, рассинхронизируется
    с видео, и в кадр попадает он один.
    """
    chain = build_filter(plan_frame(*SRC, *OUT, framing), framing, *OUT)
    for step in chain.split(";"):
        assert step.startswith("["), f"ветка не из входа: {step}"


# --- описание для интерфейса ----------------------------------------------


def test_describe_mentions_both_numbers() -> None:
    text = describe(plan("balanced"))
    assert "42%" in text
    assert "25%" in text


def test_describe_full_bleed() -> None:
    assert "целиком" in describe(plan("fill"))
