"""Выбор кодировщика: процессор или блок видеокарты.

BAZA.md §43, §53. Скорость стоит в приоритетах ниже качества, поэтому выбор
остаётся за человеком, а по умолчанию берётся качество.

Замер на встроенной графике AMD (тот же фрагмент, та же раскладка с размытием,
20 секунд видео): процессор 10 с, VAAPI 6 с. Размытие в обоих случаях считается
на процессоре — ускоряется только сжатие, поэтому выигрыш не кратный.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Узел рендера. Есть — значит видеокарта доступна процессу.
RENDER_NODE = Path("/dev/dri/renderD128")

#: Битрейт для аппаратного кодировщика. У него нет режима постоянного качества
#: вроде CRF, поэтому качество задаётся потоком. 6 Мбит/с на 1080x1920 —
#: примерно то же, что даёт libx264 при CRF 21 на нашем материале.
HARDWARE_BITRATE = "6M"


@dataclass(frozen=True)
class Encoder:
    name: str
    label: str
    #: Чем этот выбор хорош и чем плох — показывается человеку целиком,
    #: чтобы решение принималось со знанием обеих сторон.
    pros: str
    cons: str


ENCODERS: tuple[Encoder, ...] = (
    Encoder(
        "cpu", "Процессор (качество)",
        "лучшее качество при том же весе файла; работает везде",
        "медленнее примерно вдвое; сильнее греет и нагружает машину",
    ),
    Encoder(
        "gpu", "Видеокарта (скорость)",
        "быстрее на 40%; процессор остаётся свободным",
        "качество ниже при том же весе — заметнее всего на градиентах "
        "и в динамике, то есть на геймплее; работает не на всех машинах",
    ),
)


def describe_encoders() -> list[dict[str, object]]:
    """Список кодировщиков для интерфейса — с причиной недоступности."""
    return [
        {
            "name": e.name,
            "label": e.label,
            "pros": e.pros,
            "cons": e.cons,
            "available": available(e.name) is None,
            "note": available(e.name) or "",
        }
        for e in ENCODERS
    ]


def available(encoder: str = "cpu") -> str | None:
    """Причина, по которой кодировщик недоступен. None — доступен."""
    if encoder == "cpu":
        return None
    if encoder != "gpu":
        return f"неизвестный кодировщик {encoder}"
    if not RENDER_NODE.exists():
        return f"нет узла видеокарты {RENDER_NODE}"
    try:
        listing = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return f"не удалось опросить ffmpeg: {exc}"
    if "h264_vaapi" not in listing:
        return "ffmpeg собран без h264_vaapi"
    return None


def video_args(encoder: str, *, crf: int, pix_fmt: str) -> tuple[list[str], list[str], str]:
    """Аргументы кодировщика: до входа, после фильтров, и хвост цепочки фильтров.

    Возвращает тройку, потому что аппаратный путь требует всех трёх: устройство
    объявляется до входного файла, кадры загружаются в память видеокарты
    последним фильтром, и только потом идёт сам кодировщик.
    """
    if encoder != "gpu" or available("gpu") is not None:
        return [], ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
                    "-pix_fmt", pix_fmt], ""
    return (
        ["-vaapi_device", str(RENDER_NODE)],
        ["-c:v", "h264_vaapi", "-b:v", HARDWARE_BITRATE],
        ",format=nv12,hwupload",
    )
