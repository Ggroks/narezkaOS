"""Пересчёт транскрипта и клипов в выходное время.

BAZA.md §16, §44. Обязательная пара к вырезке пауз: включить вырезку без
этого пересчёта значит гарантированно рассинхронить субтитры. Поэтому
модуль появляется вместе с `narezka/core/silence.py`, а не позже.

Что пересчитывается:

- **слова** — по ним строится подсветка в субтитрах, и ошибка в четверть
  секунды видна на экране;
- **сегменты** — границы строк субтитров;
- **клипы** — границы нарезки и точка пика.

Слово, целиком попавшее в вырезанное, удаляется. Слово, задетое краем,
сужается: его звук частично остался, и выбросить его значит потерять
подсветку там, где зритель слышит речь.
"""

from __future__ import annotations

from typing import Any

from narezka.core.edl import Edl


def remap_words(words: list[dict[str, Any]], edl: Edl) -> list[dict[str, Any]]:
    """Слова в выходном времени. Пропавшие удаляются, задетые сужаются."""
    if edl.is_identity:
        return list(words)

    result: list[dict[str, Any]] = []
    for word in words:
        start, end = word.get("start"), word.get("end")
        if start is None or end is None:
            continue
        shifted = edl.shift_span(float(start), float(end))
        if shifted is None:
            continue
        moved = dict(word)
        moved["start"], moved["end"] = shifted
        # Исходное время сохраняется рядом: по нему ищут кадр в исходнике
        # и сверяют расшифровку, восстановить его вычитанием нельзя.
        #
        # Записывается место, откуда слово пришло **после** сужения, а не
        # его первоначальные границы. У слова, задетого краем вырезки, начало
        # осталось в вырезанном, и хранить его значит указывать на кадр,
        # которого в ролике нет. На пятичасовой записи так разошлись шесть
        # слов из 36 694 — нашлось проверкой verify на реальных данных.
        moved["source_start"] = edl.to_source(shifted[0])
        moved["source_end"] = edl.to_source(shifted[1])
        result.append(moved)
    return result


def remap_segments(segments: list[dict[str, Any]], edl: Edl) -> list[dict[str, Any]]:
    """Сегменты транскрипта в выходном времени.

    Границы сегмента берутся по уцелевшим словам, а не пересчитываются
    отдельно: иначе строка субтитра могла бы начинаться раньше первого
    слова, которое в ней осталось.
    """
    if edl.is_identity:
        return list(segments)

    result: list[dict[str, Any]] = []
    for segment in segments:
        words = remap_words(segment.get("words") or [], edl)
        if words:
            start = min(w["start"] for w in words)
            end = max(w["end"] for w in words)
        else:
            # Сегмент без разметки по словам: границы переносим целиком.
            shifted = edl.shift_span(
                float(segment.get("start", 0.0)), float(segment.get("end", 0.0))
            )
            if shifted is None:
                continue
            start, end = shifted

        moved = dict(segment)
        moved["start"], moved["end"] = start, end
        moved["source_start"] = edl.to_source(start)
        moved["source_end"] = edl.to_source(end)
        if segment.get("words") is not None:
            moved["words"] = words
        result.append(moved)
    return result


def remap_clips(clips: list[dict[str, Any]], edl: Edl) -> list[dict[str, Any]]:
    """Клипы в выходном времени.

    Клип, от которого после вырезки ничего не осталось, удаляется целиком:
    отрендерить его нечем. Такое возможно только если весь его диапазон был
    тишиной, то есть кандидат и так был ошибочным.
    """
    if edl.is_identity:
        return list(clips)

    result: list[dict[str, Any]] = []
    for clip in clips:
        shifted = edl.shift_span(float(clip.get("start", 0.0)), float(clip.get("end", 0.0)))
        if shifted is None:
            continue
        start, end = shifted
        moved = dict(clip)
        moved["start"], moved["end"] = start, end
        moved["duration"] = end - start
        moved["source_start"] = edl.to_source(start)
        moved["source_end"] = edl.to_source(end)

        peak = clip.get("peak_at")
        if peak is not None:
            # Пик мог попасть в вырезанное. Тогда он прижимается к началу
            # клипа, а не выбрасывается: клип остаётся, и точка отсчёта
            # ему всё равно нужна.
            moved_peak = edl.to_output(float(peak))
            moved["peak_at"] = moved_peak if moved_peak is not None else start
        return_words = clip.get("words")
        if return_words:
            moved["words"] = remap_words(return_words, edl)
        result.append(moved)
    return result


def verify(words: list[dict[str, Any]], edl: Edl, *, tolerance: float = 0.05) -> list[str]:
    """Ищет рассинхрон после пересчёта. Пустой список — всё сходится.

    Проверка отдельной функцией, а не утверждением внутри пересчёта: её
    можно запустить на реальной записи и получить список расхождений,
    а не падение на первом же.
    """
    problems: list[str] = []
    previous_end = -1.0
    for word in words:
        start, end = word["start"], word["end"]
        if end < start - tolerance:
            problems.append(f"слово «{word.get('word', '?')}»: конец раньше начала")
        if start < previous_end - tolerance:
            problems.append(f"слово «{word.get('word', '?')}»: перекрывает предыдущее")
        previous_end = max(previous_end, end)

        source = word.get("source_start")
        if source is not None:
            back = edl.to_source(start)
            if abs(back - float(source)) > tolerance:
                problems.append(
                    f"слово «{word.get('word', '?')}»: обратный пересчёт даёт "
                    f"{back:.3f} вместо {float(source):.3f}"
                )
    return problems
