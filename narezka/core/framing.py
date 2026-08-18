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
#: single — исходник на подложке; split — вебка сверху и контент снизу;
#: track — узкий кроп, ведомый за лицом (§17); pip — лицо врезкой поверх
#: контента; camera — только вебка во весь кадр.
Layout = Literal["single", "split", "track", "pip", "camera"]
Background = Literal["blur", "color"]

#: Раскладки, которым нужна найденная вебка. Без неё они неприменимы, и
#: интерфейс их не предлагает: обещать раскладку, для которой нет данных,
#: значит обещать несбыточное.
CAMERA_LAYOUTS = ("split", "pip", "camera")

#: Углы для врезки. Значения — смещение по осям в долях свободного места:
#: (0,0) — левый верхний, (1,1) — правый нижний.
PIP_CORNERS: dict[str, tuple[int, int]] = {
    "top_left": (0, 0),
    "top_right": (1, 0),
    "bottom_left": (0, 1),
    "bottom_right": (1, 1),
}

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


#: Какую долю высоты кадра отдавать вебке в раскладке «сплит». Треть —
#: лицо крупное и читаемое, но большая часть кадра остаётся контенту, ради
#: которого ролик и смотрят.
SPLIT_TOP_SHARE = 0.34

#: Границы доли: ниже лицо перестаёт читаться на телефоне, выше контент
#: становится слишком мелким.
SPLIT_MIN_SHARE = 0.2
SPLIT_MAX_SHARE = 0.5

#: Во сколько раз кадр вокруг лица шире самого лица. Портрет с запасом
#: на плечи и фон; вплотную к лицу смотреть неприятно.
FACE_ZOOM_OUT = 2.6

#: Пределы приближения. Меньше полутора — в кадре одно лицо без плеч,
#: и любое движение головы выносит его за край. Больше четырёх — вебка
#: показана целиком вместе с комнатой, то есть приближения нет.
FACE_ZOOM_MIN = 1.5
FACE_ZOOM_MAX = 4.0

#: Куда попадает центр лица по высоте полосы. Чуть выше середины — так
#: в кадр входят плечи, а не пустота над головой.
FACE_VERTICAL_ANCHOR = 0.45

#: Доля ширины кадра, которую занимает врезка с лицом. Треть: меньше —
#: лицо нечитаемо на телефоне, больше — врезка спорит с содержимым за
#: внимание, а она вспомогательная.
PIP_WIDTH_SHARE = 0.33

#: Отступ врезки от края, в долях её ширины. Впритык к краю она выглядит
#: приклеенной, а слишком далеко — теряет связь с углом.
PIP_MARGIN_SHARE = 0.12

#: Готовые степени приближения. Названы тем, что видно в кадре, а не
#: числами: «2.0» не говорит ничего, пока не увидишь.
FACE_PRESETS: dict[str, dict[str, Any]] = {
    "close": {"title": "Крупно", "note": "Лицо во всю полосу, плечи почти не видны", "zoom": 1.8},
    "portrait": {"title": "Портрет", "note": "Лицо и плечи — как в обычной вебке", "zoom": 2.6},
    "wide": {"title": "Свободно", "note": "Видно и обстановку вокруг", "zoom": 3.6},
}


def face_zoom_presets() -> list[dict[str, Any]]:
    return [{"name": key, **value} for key, value in FACE_PRESETS.items()]


@dataclass(frozen=True)
class SplitPlan:
    """Раскладка «сплит»: вебка сверху, приближённый контент снизу (§61)."""

    cam_crop: tuple[int, int, int, int]     # что вырезать под вебку
    cam_height: int                          # её высота в готовом кадре
    main_crop: tuple[int, int, int, int]     # что вырезать под контент
    main_height: int


@dataclass(frozen=True)
class Framing:
    layout: Layout = "single"
    preset: str = "balanced"
    #: Используется при preset="custom". Доля ширины, отрезаемая суммарно.
    side_crop: float = 0.25
    anchor: Anchor = "center"
    #: Настройки слежения. Живут здесь же, потому что рамка строится из
    #: одного набора: раскладка и её параметры не должны разъезжаться
    #: по разным местам.
    track_samples_per_second: float = 5.0
    track_smoothing: float = 0.6
    track_dead_zone: float = 0.0
    track_max_speed: float = 3.0
    #: Сплит: доля высоты под вебку и приближение лица.
    split_top_share: float = SPLIT_TOP_SHARE
    face_zoom: float = FACE_ZOOM_OUT
    face_vertical: float = FACE_VERTICAL_ANCHOR
    #: Вести ли кадр за головой там, где показана вебка.
    follow_face: bool = False
    #: Врезка: размер, отступ и угол.
    pip_share: float = PIP_WIDTH_SHARE
    pip_margin: float = PIP_MARGIN_SHARE
    pip_corner: str = "top_left"
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


def build_track_filter(
    crop_x: str,
    crop_w: int,
    crop_h: int,
    out_w: int,
    out_h: int,
    subtitle_name: str | None = None,
    fonts_dir: str | None = None,
    fps: int | None = None,
) -> str:
    """Цепочка для слежения: кроп с подвижной левой границей.

    `crop_x` — выражение от времени, ffmpeg вычисляет его на каждом кадре.
    Кроп по высоте не двигается: вертикальное движение головы мелкое, а
    рамка, гуляющая вверх-вниз, читается как тряска камеры.
    """
    decimate = f"fps={fps}," if fps else ""
    chain = (
        f"[0:v]{decimate}crop={crop_w}:{crop_h}:'{crop_x}':0,"
        f"scale={out_w}:{out_h}"
    )
    if subtitle_name:
        fonts = f":fontsdir={fonts_dir}" if fonts_dir else ""
        chain += f",subtitles={subtitle_name}{fonts}"
    return chain + "[v]"


def plan_camera(
    source_w: int,
    source_h: int,
    out_w: int,
    out_h: int,
    cam: tuple[int, int, int, int],
    *,
    face: tuple[int, int, int, int] | None = None,
    zoom: float = FACE_ZOOM_OUT,
    vertical: float = FACE_VERTICAL_ANCHOR,
) -> tuple[int, int, int, int] | None:
    """Что вырезать для раскладки «только вебка».

    Весь кадр отдан стримеру: содержимого не видно вовсе. Нужно там, где
    ролик держится на реакции, а не на том, что происходит на экране —
    рассказ, ответ на вопрос, эмоция. Сплит в таком случае отдаёт две трети
    кадра картинке, которая ничего не добавляет.

    Кадрируется по лицу, если оно найдено: окно вебки ищется грубо и
    захватывает рамку оверлея, а лицо в нём обычно не по центру.
    """
    cam_x, cam_y, cam_w, cam_h = cam
    if cam_w <= 0 or cam_h <= 0 or out_h <= 0:
        return None

    ratio = out_w / out_h
    if face is not None:
        crop = _frame_face(face, ratio, source_w, source_h, zoom=zoom, vertical=vertical)
    else:
        fitted_w, fitted_h = _fit_ratio(cam_w, cam_h, ratio)
        crop = (
            _even(cam_x + (cam_w - fitted_w) / 2),
            _even(cam_y + (cam_h - fitted_h) / 2),
            _even(fitted_w),
            _even(fitted_h),
        )
    return _clamp_crop(crop, source_w, source_h)


def build_camera_filter(
    crop: tuple[int, int, int, int],
    out_w: int,
    out_h: int,
    subtitle_name: str | None = None,
    fonts_dir: str | None = None,
    fps: int | None = None,
    crop_x: str | None = None,
) -> str:
    """Цепочка для раскладки «только вебка»: один вырез на весь кадр.

    `crop_x` — выражение от времени, если кадр ведётся за головой. Иначе
    вырез стоит на месте, взятый по одному кадру клипа.
    """
    decimate = f"fps={fps}," if fps else ""
    x, y, w, h = crop
    left = f"'{crop_x}'" if crop_x else str(x)
    chain = f"[0:v]{decimate}crop={w}:{h}:{left}:{y},scale={out_w}:{out_h}"
    if subtitle_name:
        fonts = f":fontsdir={fonts_dir}" if fonts_dir else ""
        chain += f",subtitles={subtitle_name}{fonts}"
    return chain + "[v]"


def build_filter(
    plan: FramePlan,
    framing: Framing,
    out_w: int,
    out_h: int,
    subtitle_name: str | None = None,
    fonts_dir: str | None = None,
    fps: int | None = None,
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

    # Прореживание кадров идёт первым фильтром, до масштабирования и
    # размытия: иначе вся тяжёлая обработка считается по всем кадрам, а
    # лишние выбрасываются в самом конце. Замер на 720p60: ограничение
    # в начале цепочки снимает треть времени, в конце — не даёт ничего.
    decimate = f"fps={fps}," if fps else ""
    content = f"[0:v]{decimate}{source_crop},scale={plan.scaled_w}:{plan.scaled_h}"

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
            f"[0:v]{decimate}scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
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


def plan_split(
    source_w: int,
    source_h: int,
    out_w: int,
    out_h: int,
    cam: tuple[int, int, int, int],
    *,
    face: tuple[int, int, int, int] | None = None,
    content: tuple[int, int, int, int] | None = None,
    top_share: float = SPLIT_TOP_SHARE,
    anchor: Anchor = "center",
    face_zoom: float = FACE_ZOOM_OUT,
    face_vertical: float = FACE_VERTICAL_ANCHOR,
) -> SplitPlan | None:
    """Раскладка «сплит» по найденному окну вебки.

    Сверху лицо стримера, снизу — приближённый контент. Смысл в том, чтобы
    зритель одновременно видел и то, что смотрят, и реакцию: при обычном
    кадрировании вебка в углу экрана обрезается и реакция теряется.

    Контент внизу режется **в обход окна вебки**: показывать её дважды
    незачем, а в исходном кадре она занимает угол.
    """
    cam_x, cam_y, cam_w, cam_h = cam
    if cam_w <= 0 or cam_h <= 0:
        return None

    share = min(max(top_share, SPLIT_MIN_SHARE), SPLIT_MAX_SHARE)
    cam_height = _even(out_h * share)
    main_height = out_h - cam_height
    if main_height <= 0:
        return None

    target_ratio = out_w / cam_height

    if face is not None:
        # Кадрируем по лицу, а не по найденному окну: окно ищется грубо,
        # по углам кадра, и захватывает панель браузера или край экрана.
        cam_crop = _frame_face(
            face, target_ratio, source_w, source_h,
            zoom=face_zoom, vertical=face_vertical,
        )
    else:
        # Без лица подрезаем само окно под пропорцию полосы, чтобы не
        # искажать картинку растяжением.
        fitted_w, fitted_h = _fit_ratio(cam_w, cam_h, target_ratio)
        cam_crop = (
            _even(cam_x + (cam_w - fitted_w) / 2),
            _even(cam_y + (cam_h - fitted_h) / 2),
            _even(fitted_w),
            _even(fitted_h),
        )

    # Контент: найденная область проигрываемого видео, иначе — часть кадра,
    # свободная от вебки. Без области нижняя полоса режется по центру
    # и захватывает интерфейс: панель плеера, ленту сообщений, поля страницы.
    if content is not None:
        region_x, region_y, region_w, region_h = content
    else:
        region_x, region_w = 0, source_w
        region_y = cam_y + cam_h if cam_y < source_h * 0.25 else 0
        region_h = source_h - region_y
        if region_h < source_h * 0.4:
            region_y, region_h = 0, source_h

    main_ratio = out_w / main_height
    main_w, main_h = _fit_ratio(region_w, region_h, main_ratio)
    if anchor == "left":
        main_x = region_x
    elif anchor == "right":
        main_x = region_x + region_w - main_w
    else:
        main_x = region_x + (region_w - main_w) // 2

    main_crop = (
        _even(main_x),
        _even(region_y + (region_h - main_h) / 2),
        _even(main_w),
        _even(main_h),
    )

    return SplitPlan(
        cam_crop=_clamp_crop(cam_crop, source_w, source_h),
        cam_height=cam_height,
        main_crop=_clamp_crop(main_crop, source_w, source_h),
        main_height=main_height,
    )


def _clamp_crop(
    crop: tuple[int, int, int, int], source_w: int, source_h: int
) -> tuple[int, int, int, int]:
    """Загоняет вырез в пределы кадра.

    Нужно после округления до чётных сторон: пара пикселей вверх выталкивает
    вырез за край, и ffmpeg отказывается резать.
    """
    x, y, w, h = crop
    w = max(2, min(w, source_w))
    h = max(2, min(h, source_h))
    x = max(0, min(x, source_w - w))
    y = max(0, min(y, source_h - h))
    return (x - x % 2, y - y % 2, w - w % 2, h - h % 2)


def _frame_face(
    face: tuple[int, int, int, int],
    ratio: float,
    source_w: int,
    source_h: int,
    *,
    zoom: float = FACE_ZOOM_OUT,
    vertical: float = FACE_VERTICAL_ANCHOR,
) -> tuple[int, int, int, int]:
    """Портретный кадр вокруг лица под заданную пропорцию.

    `zoom` — во сколько раз кадр шире лица: меньше значит ближе.
    `vertical` — где в кадре оказывается центр лица по высоте.
    """
    fx, fy, fw, fh = face
    zoom = min(max(zoom, FACE_ZOOM_MIN), FACE_ZOOM_MAX)
    width = min(_even(fw * zoom), source_w)
    height = min(_even(width / ratio), source_h)
    width = _even(min(width, height * ratio))

    centre_x = fx + fw / 2
    centre_y = fy + fh / 2
    x = _even(centre_x - width / 2)
    y = _even(centre_y - height * min(max(vertical, 0.1), 0.9))

    # Кадр не должен выходить за пределы исходника.
    x = max(0, min(x, source_w - width))
    y = max(0, min(y, source_h - height))
    return (_even(x), _even(y), width, height)


def _fit_ratio(width: int, height: int, ratio: float) -> tuple[int, int]:
    """Наибольший прямоугольник заданной пропорции внутри области."""
    if width / height > ratio:
        return int(height * ratio), height
    return width, int(width / ratio)



def build_pip_filter(
    cam: tuple[int, int, int, int],
    plan: FramePlan,
    framing: Framing,
    out_w: int,
    out_h: int,
    subtitle_name: str | None = None,
    fonts_dir: str | None = None,
    fps: int | None = None,
) -> str:
    """Цепочка для врезки: лицо небольшим окном поверх содержимого.

    Отличие от сплита: там кадр делится надвое и обе части равноправны,
    здесь содержимое занимает весь экран, а лицо накладывается сверху.
    Врезка уместнее, когда важно именно содержимое — карта, таблица,
    текст, — и терять его половину ради лица не хочется.

    Размер, отступ и угол задаются настройкой. По умолчанию — левый верхний:
    правый нижний на всех площадках перекрывают кнопками, а верхний левый
    остаётся свободным. Но у площадок это меняется, а поверх содержимого
    бывает и своя важная область, поэтому угол выбирается.
    """
    decimate = f"fps={fps}," if fps else ""
    cx, cy, cw, ch = cam

    pip_w = max(2, int(out_w * framing.pip_share)) & ~1
    pip_h = max(2, int(pip_w * ch / cw)) & ~1
    margin = int(pip_w * framing.pip_margin)

    # Смещение считается от свободного места, поэтому врезка не выходит за
    # кадр ни в одном углу, каким бы большой её ни сделали.
    right, bottom = PIP_CORNERS.get(framing.pip_corner, (0, 0))
    pip_x = max(0, out_w - pip_w - margin) if right else margin
    pip_y = max(0, out_h - pip_h - margin) if bottom else margin

    background = (
        f"[0:v]{decimate}crop={plan.crop_w}:{plan.crop_h}:{plan.crop_x}:{plan.crop_y},"
        f"scale={plan.scaled_w}:{plan.scaled_h},"
        f"pad={out_w}:{out_h}:0:{plan.offset_y}:{framing.color}[bg]"
    )
    face = f"[0:v]{decimate}crop={cw}:{ch}:{cx}:{cy},scale={pip_w}:{pip_h}[pip]"
    chain = f"{background};{face};[bg][pip]overlay={pip_x}:{pip_y}"

    if subtitle_name:
        fonts = f":fontsdir={fonts_dir}" if fonts_dir else ""
        chain += f",subtitles={subtitle_name}{fonts}"
    return chain + "[v]"


def build_split_filter(
    plan: SplitPlan,
    out_w: int,
    subtitle_name: str | None = None,
    fonts_dir: str | None = None,
    fps: int | None = None,
    cam_x: str | None = None,
) -> str:
    """Цепочка фильтров для сплита: две полосы одна над другой.

    `cam_x` — выражение от времени для левой границы верхней полосы, когда
    кадр ведётся за головой. Стример за минуту уходит из статичной рамки:
    она берётся по одному кадру клипа, а он двигается — и к середине ролика
    в полосе оказывается плечо или пустой угол комнаты.

    Двигается только верхняя полоса. Нижняя — содержимое, и её дрожание
    вслед за головой читалось бы как тряска камеры.
    """
    # Как и в одиночной раскладке — прореживание до кропа и масштабирования.
    decimate = f"fps={fps}," if fps else ""
    cx, cy, cw, ch = plan.cam_crop
    mx, my, mw, mh = plan.main_crop
    cam_left = f"'{cam_x}'" if cam_x else str(cx)
    base = (
        f"[0:v]{decimate}crop={cw}:{ch}:{cam_left}:{cy},scale={out_w}:{plan.cam_height}[cam];"
        f"[0:v]{decimate}crop={mw}:{mh}:{mx}:{my},scale={out_w}:{plan.main_height}[main];"
        f"[cam][main]vstack=inputs=2"
    )
    if subtitle_name is None:
        return f"{base}[v]"
    subtitles = f"subtitles={subtitle_name}"
    if fonts_dir:
        subtitles += f":fontsdir={fonts_dir}"
    return f"{base}[base];[base]{subtitles}[v]"


def plan_pip(
    source_w: int,
    source_h: int,
    cam: tuple[int, int, int, int],
    *,
    face: tuple[int, int, int, int] | None = None,
    zoom: float = FACE_ZOOM_OUT,
    vertical: float = FACE_VERTICAL_ANCHOR,
) -> tuple[int, int, int, int] | None:
    """Что вырезать под врезку.

    По лицу, если оно найдено: окно вебки ищется по углам кадра и прихватывает
    рамку оверлея с подписями, а во врезке размером в треть экрана каждый
    лишний процент площади — это лицо мельче.
    """
    cam_x, cam_y, cam_w, cam_h = cam
    if cam_w <= 0 or cam_h <= 0:
        return None
    if face is None:
        return _clamp_crop((cam_x, cam_y, cam_w, cam_h), source_w, source_h)
    crop = _frame_face(
        face, cam_w / cam_h, source_w, source_h, zoom=zoom, vertical=vertical
    )
    return _clamp_crop(crop, source_w, source_h)


def plan_track_still(
    source_w: int,
    source_h: int,
    out_w: int,
    out_h: int,
    face: tuple[int, int, int, int] | None = None,
) -> tuple[int, int, int, int]:
    """Вырез слежения, застывший на одном кадре.

    Слежение — это движение, и на неподвижной картинке его не показать.
    Но показать, **что видно в кадре**, можно: рамка той же ширины, что
    в ролике, стоящая там, где сейчас голова. Предпросмотр обычной
    раскладки вместо неё врал бы сильнее.
    """
    crop_h = source_h
    crop_w = _even(min(crop_h * out_w / out_h, source_w))
    centre = face[0] + face[2] / 2 if face else source_w / 2
    x = min(max(centre - crop_w / 2, 0), max(0, source_w - crop_w))
    return _clamp_crop((_even(x), 0, crop_w, crop_h), source_w, source_h)


def build_layout_filter(
    framing: Framing,
    plan: FramePlan,
    out_w: int,
    out_h: int,
    *,
    split: SplitPlan | None = None,
    split_x: str | None = None,
    track: dict[str, Any] | None = None,
    pip: tuple[int, int, int, int] | None = None,
    camera: tuple[int, int, int, int] | None = None,
    camera_x: str | None = None,
    subtitle_name: str | None = None,
    fonts_dir: str | None = None,
    fps: int | None = None,
) -> str:
    """Цепочка фильтров для выбранной раскладки.

    Одна на всех: и сборка ролика, и предпросмотр зовут её. Раньше выбор
    раскладки был записан дважды — в рендере и в предпросмотре, — и они уже
    разошлись: предпросмотр кадрирования показывал обычную раскладку, что бы
    человек ни выбрал. Настраивать по картинке, которой не будет в ролике,
    хуже, чем не показывать картинку вовсе.

    Раскладка, для которой не нашлось данных (нет вебки, не посчитана
    траектория), сюда приходит пустой — и кадр строится обычным способом.
    Сообщить об этом должен тот, кто данные собирал: здесь уже неизвестно,
    просили ли раскладку вообще.
    """
    if pip is not None:
        return build_pip_filter(
            pip, plan, framing, out_w, out_h, subtitle_name, fonts_dir=fonts_dir, fps=fps
        )
    if camera is not None:
        return build_camera_filter(
            camera, out_w, out_h, subtitle_name,
            fonts_dir=fonts_dir, fps=fps, crop_x=camera_x,
        )
    if track is not None:
        return build_track_filter(
            track["x"], track["w"], track["h"], out_w, out_h, subtitle_name,
            fonts_dir=fonts_dir, fps=fps,
        )
    if split is not None:
        return build_split_filter(
            split, out_w, subtitle_name, fonts_dir=fonts_dir, fps=fps, cam_x=split_x
        )
    return build_filter(plan, framing, out_w, out_h, subtitle_name, fonts_dir=fonts_dir, fps=fps)


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
