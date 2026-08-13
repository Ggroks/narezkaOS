"""Геометрия вертикального кадра.

BAZA.md §17 и §61. Модуль чистый: на входе размеры и настройки, на выходе
числа и строка фильтра. Никаких файлов и вызовов ffmpeg.

Ключевая зависимость, которую стоит понимать при настройке: **обрезка по бокам
и высота контента — один и тот же параметр**. Исходник вписывается по ширине
кадра, поэтому чем уже он становится после обрезки, тем выше выглядит
в вертикальном кадре. Отрезать нечего — контент занимает узкую полосу;
отрезать много — контент заполняет кадр, но часть картинки теряется.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Anchor = Literal["center", "left", "right"]
Background = Literal["blur", "color"]

#: Готовые варианты обрезки по бокам. Значения — доля ширины, которая
#: отрезается суммарно с двух сторон.
#:
#: Здесь только варианты с фиксированной величиной. «fill» считается из
#: пропорций исходника, «custom» берётся из настройки — они разбираются
#: отдельно, и держать их тут значением-заглушкой опасно: неизвестное имя
#: пресета тогда молча получало бы максимальную обрезку.
PRESETS: dict[str, float] = {
    # Ничего не теряется. Содержимое занимает узкую полосу.
    "full": 0.0,
    # Умеренный компромисс: края обычно несут меньше смысла.
    "balanced": 0.25,
    # Заметно крупнее, но края уже теряются ощутимо.
    "focus": 0.5,
}

#: Порядок для интерфейса — от бережного к агрессивному.
PRESET_ORDER = ("full", "balanced", "focus", "fill")

PRESET_LABELS = {
    "full": "Целиком",
    "balanced": "Сбалансированно",
    "focus": "Крупно",
    "fill": "Во весь кадр",
    "custom": "Вручную",
}


@dataclass(frozen=True)
class Framing:
    preset: str = "balanced"
    #: Используется при preset="custom". Доля ширины, отрезаемая суммарно.
    side_crop: float = 0.25
    anchor: Anchor = "center"
    background: Background = "blur"
    blur_sigma: float = 28.0
    color: str = "0x14171c"


@dataclass(frozen=True)
class FramePlan:
    """Готовый расчёт кадра."""

    crop_w: int
    crop_h: int
    crop_x: int
    crop_y: int
    scaled_w: int
    scaled_h: int
    offset_y: int
    #: Какую долю высоты кадра занимает содержимое.
    content_share: float
    #: Какая доля исходной ширины потеряна обрезкой.
    lost_share: float
    #: Заполняет ли содержимое кадр целиком — тогда подложки не видно.
    full_bleed: bool


def _even(value: float) -> int:
    """Чётный размер: h264 с yuv420p не принимает нечётные стороны."""
    return max(int(round(value / 2)) * 2, 2)


def fill_side_crop(source_w: int, source_h: int, out_w: int, out_h: int) -> float:
    """Обрезка, при которой содержимое заполняет кадр целиком."""
    if source_w <= 0 or source_h <= 0 or out_h <= 0:
        return 0.0
    needed_w = out_w * source_h / out_h
    if needed_w >= source_w:
        # Исходник уже уже кадра — обрезать нечего.
        return 0.0
    return 1.0 - needed_w / source_w


def resolve_side_crop(framing: Framing, source_w: int, source_h: int, out_w: int, out_h: int) -> float:
    """Величина обрезки с учётом выбранного варианта."""
    if framing.preset == "custom":
        value = framing.side_crop
    elif framing.preset == "fill":
        value = fill_side_crop(source_w, source_h, out_w, out_h)
    else:
        # Незнакомое имя — не режем ничего: терять картинку из-за опечатки
        # в конфиге хуже, чем получить узкую полосу.
        value = PRESETS.get(framing.preset, 0.0)
    # Обрезать всё нельзя: от кадра должно что-то остаться.
    return min(max(value, 0.0), 0.95)


def plan_frame(
    source_w: int,
    source_h: int,
    out_w: int,
    out_h: int,
    framing: Framing,
) -> FramePlan:
    """Считает, что вырезать из исходника и куда поставить."""
    side_crop = resolve_side_crop(framing, source_w, source_h, out_w, out_h)

    crop_w = _even(source_w * (1.0 - side_crop))
    crop_w = min(crop_w, source_w)
    crop_h = source_h

    if framing.anchor == "left":
        crop_x = 0
    elif framing.anchor == "right":
        crop_x = source_w - crop_w
    else:
        crop_x = (source_w - crop_w) // 2
    crop_x = max(crop_x - crop_x % 2, 0)

    scaled_w = out_w
    scaled_h = _even(out_w * crop_h / crop_w)

    crop_y = 0
    if scaled_h > out_h:
        # Содержимое выше кадра — лишнее срезается сверху и снизу поровну.
        visible_source_h = crop_h * out_h / scaled_h
        crop_y = _even((crop_h - visible_source_h) / 2)
        crop_h = _even(visible_source_h)
        scaled_h = out_h

    offset_y = max((out_h - scaled_h) // 2, 0)

    return FramePlan(
        crop_w=crop_w,
        crop_h=crop_h,
        crop_x=crop_x,
        crop_y=crop_y,
        scaled_w=scaled_w,
        scaled_h=scaled_h,
        offset_y=offset_y,
        content_share=min(scaled_h / out_h, 1.0),
        lost_share=side_crop,
        full_bleed=scaled_h >= out_h,
    )


def build_filter(
    plan: FramePlan,
    framing: Framing,
    out_w: int,
    out_h: int,
    subtitle_name: str | None = None,
    fonts_dir: str | None = None,
) -> str:
    """Строка filter_complex для ffmpeg.

    Без `subtitle_name` цепочка та же, но без вшивания субтитров — так
    предпросмотр кадра в интерфейсе показывает ровно ту же геометрию, что
    получится при рендере, и не требует готовых файлов субтитров.

    `fonts_dir` передаётся libass, чтобы шрифт брался из поставки, а не
    подбирался fontconfig на машине рендера (§60).

    Когда содержимое заполняет кадр, подложка не строится вовсе: считать
    размытие, которого не будет видно, — впустую потраченное время кодирования.
    """
    source_crop = f"crop={plan.crop_w}:{plan.crop_h}:{plan.crop_x}:{plan.crop_y}"

    content = f"[0:v]{source_crop},scale={plan.scaled_w}:{plan.scaled_h}"

    if plan.full_bleed:
        base = content
    elif framing.background == "color":
        # Однотонные полосы делает pad, а не color + overlay. Отдельный
        # источник color идёт по своим меткам времени, и overlay успевает
        # выдать первый кадр раньше, чем подъедет кадр видео — на коротком
        # отрезке в кадр попадает одна заливка. pad второго входа не заводит,
        # синхронизировать нечего, и считается он дешевле.
        base = f"{content},pad={out_w}:{out_h}:0:{plan.offset_y}:{framing.color}"
    else:
        backdrop = (
            f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h}"
        )
        if framing.blur_sigma > 0:
            backdrop += f",gblur=sigma={framing.blur_sigma:g}"
        # Оба потока растут из [0:v], поэтому идут по одним меткам времени.
        base = f"{backdrop}[bg];{content}[fg];[bg][fg]overlay=(W-w)/2:{plan.offset_y}"

    if subtitle_name is None:
        return f"{base}[v]"

    subtitles = f"subtitles={subtitle_name}"
    if fonts_dir:
        subtitles += f":fontsdir={fonts_dir}"
    return f"{base}[base];[base]{subtitles}[v]"


def preview_presets(source_w: int, source_h: int, out_w: int, out_h: int) -> list[dict[str, Any]]:
    """Что даст каждый готовый вариант на этом конкретном исходнике.

    Нужно интерфейсу: доли зависят от пропорций записи, поэтому подписывать
    кнопки постоянными числами нельзя — для 16:9 и для 4:3 они разные.
    """
    result = []
    for name in PRESET_ORDER:
        plan = plan_frame(source_w, source_h, out_w, out_h, Framing(preset=name))
        result.append(
            {
                "preset": name,
                "label": PRESET_LABELS[name],
                "side_crop": round(plan.lost_share, 4),
                "content_share": round(plan.content_share, 4),
                "full_bleed": plan.full_bleed,
                "summary": describe(plan),
            }
        )
    return result


def describe(plan: FramePlan) -> str:
    """Короткое человекочитаемое описание для лога и интерфейса."""
    if plan.full_bleed:
        return f"кадр заполнен целиком, по бокам потеряно {plan.lost_share * 100:.0f}%"
    return (
        f"содержимое занимает {plan.content_share * 100:.0f}% высоты, "
        f"по бокам потеряно {plan.lost_share * 100:.0f}%"
    )
