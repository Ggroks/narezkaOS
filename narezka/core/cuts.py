"""Применение правок оси времени внутри клипа средствами ffmpeg.

BAZA.md §16, §37. Ось времени (`narezka/core/edl.py`) говорит, что вырезать;
здесь это превращается в фильтры, которые ffmpeg действительно исполнит.

**Почему `select`, а не нарезка с последующей склейкой.** Склейка потребовала
бы промежуточных файлов на каждый кусок и отдельного прохода на объединение —
для трёх-четырёх вырезок в минутном ролике это втрое больше работы и втрое
больше мест, где что-то останется на диске после сбоя. `select` отбирает
кадры одним проходом внутри уже существующей цепочки.

**Почему обязателен `setpts`.** `select` выбрасывает кадры, но оставляет их
временные метки: без пересборки меток ffmpeg честно сохранит дыры, и в ролике
на месте вырезки будет стоп-кадр нужной длины. Ровно та же ловушка со звуком —
`asetpts`. Ошибка тихая: файл собирается, ничего не падает, а результат
неправильный.
"""

from __future__ import annotations

from narezka.core.edl import EPS, Edl


def spans_within(edl: Edl, start: float, end: float) -> list[tuple[float, float]]:
    """Уцелевшие куски клипа [start, end) в **относительном** времени клипа.

    Относительном потому, что ffmpeg получает клип уже вырезанным по `-ss`,
    и время внутри него идёт от нуля, а не от начала записи.
    """
    if edl.is_identity:
        return [(0.0, end - start)]

    pieces: list[tuple[float, float]] = []
    for span in edl.spans:
        left = max(span.start, start)
        right = min(span.end, end)
        if right - left > EPS:
            pieces.append((left - start, right - start))
    return pieces


def build_select(pieces: list[tuple[float, float]], total: float) -> tuple[str, str]:
    """Фильтры отбора кадров и звука. Пустая пара — вырезать нечего.

    Возвращаются два выражения, а не одно: видео и звук отбираются разными
    фильтрами, и пропустить второй значит получить рассинхрон звука с
    картинкой — самую заметную из возможных ошибок.
    """
    if not pieces:
        return "", ""
    # Клип уцелел целиком — фильтры не нужны. Лишний select стоил бы
    # прохода по всем кадрам ради тождественного результата.
    if len(pieces) == 1 and pieces[0][0] <= EPS and pieces[0][1] >= total - EPS:
        return "", ""

    condition = "+".join(
        f"between(t,{start:.3f},{end:.3f})" for start, end in pieces
    )
    # N/FRAME_RATE/TB для видео и N/SR/TB для звука — пересборка меток от нуля
    # с шагом кадра и отсчёта соответственно.
    video = f"select='{condition}',setpts=N/FRAME_RATE/TB"
    audio = f"aselect='{condition}',asetpts=N/SR/TB"
    return video, audio


def clip_cuts(edl: Edl, start: float, end: float) -> tuple[str, str, float]:
    """Фильтры для клипа и его длительность после вырезок.

    Длительность возвращается потому, что она перестаёт равняться `end-start`,
    а ffmpeg получает её отдельным параметром `-t`: без пересчёта он оборвал бы
    ролик раньше конца или дописал пустоту.
    """
    total = end - start
    if edl.is_identity:
        return "", "", total

    pieces = spans_within(edl, start, end)
    kept = sum(right - left for left, right in pieces)
    video, audio = build_select(pieces, total)
    return video, audio, kept
