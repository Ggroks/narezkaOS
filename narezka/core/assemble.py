"""Склейка отрезков записи в один длинный ролик.

BAZA.md §20, §37.

**Почему через временные куски, а не одним проходом.** Первая версия строила
цепочку `trim`+`concat` в один вызов ffmpeg — без файлов на диске. На сюжетном
режиме это работало (три куска подряд), а на подборке лучших моментов съело
всю память машины.

Причина в порядке. `concat` требует куски в заданной последовательности, а
входной файл читается по порядку — и когда зацепка снята на сто двадцатой
минуте, а стоит первой, ffmpeg вынужден держать раскодированное до тех пор,
пока склейка до него не дойдёт. При двадцати восьми кусках вразнобой это
гигабайты сырых кадров.

Поэтому каждый кусок вырезается отдельным вызовом, а потом они соединяются
демультиплексором `concat` копированием потока. Памяти нужно на один кусок,
сколько бы их ни было; расплата — временные файлы, но они живут в каталоге
задачи и убираются вместе с ним.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def total_duration(pieces: list[tuple[float, float]]) -> float:
    return sum(max(0.0, end - start) for start, end in pieces)


def cut_piece(
    source: Path,
    start: float,
    end: float,
    target: Path,
    *,
    width: int,
    height: int,
    crf: int,
    pix_fmt: str,
    fps: int | None = None,
    timeout: float = 900.0,
) -> None:
    """Вырезает один кусок в отдельный файл.

    Все куски приводятся к одному размеру, частоте кадров и параметрам звука.
    Без этого демультиплексор `concat` откажется их соединять: он умеет
    склеивать только однородные потоки, а запись может менять разрешение
    посреди себя — стример переключил сцену, и половина кусков другая.
    """
    decimate = f"fps={fps}," if fps else ""
    chain = (
        f"{decimate}scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    # -ss до -i: быстрая перемотка по контейнеру. Точность даёт перекодирование,
    # которое здесь всё равно происходит.
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end - start:.3f}",
            "-vf", chain,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-pix_fmt", pix_fmt,
            # Частота дискретизации и число каналов тоже приводятся к общему:
            # разнобой в звуке ломает склейку так же, как разнобой в картинке.
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-f", "mp4", str(target),
        ],
        check=True, capture_output=True, timeout=timeout,
    )


def concat_list(paths: list[Path]) -> str:
    """Содержимое файла-описи для демультиплексора `concat`.

    Одинарные кавычки внутри пути экранируются: без этого путь с апострофом
    разорвёт строку описи, и ffmpeg прочитает мусор.
    """
    lines = []
    for path in paths:
        escaped = str(path.resolve()).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def join_pieces(paths: list[Path], listing: Path, target: Path, *, timeout: float = 1800.0) -> None:
    """Соединяет готовые куски копированием потока.

    Копированием, а не перекодированием: куски уже приведены к общему виду,
    и второй проход кодирования только испортил бы картинку и потратил время.
    """
    listing.write_text(concat_list(paths), encoding="utf-8")
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(target),
        ],
        check=True, capture_output=True, timeout=timeout,
    )
