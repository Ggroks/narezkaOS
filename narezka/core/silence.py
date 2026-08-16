"""Поиск пауз, которые можно вырезать без вреда.

BAZA.md §16. Стадия чистая: на вход громкость по окнам и слова транскрипта,
на выход — список правок (`narezka/core/edl.py`). Ничего не читает и не пишет,
поэтому проверяется целиком.

**Почему не «всё, что тише порога».** Так режут монтажёры-новички, и результат
узнаётся сразу: речь становится тараторящей, шутки перестают работать. Пауза
перед ответом — часть высказывания, а не пустое место. Поэтому вырезается не
любая тишина, а только та, что:

- длиннее порога, ниже которого пауза несёт смысл;
- не примыкает вплотную к слову — иначе рез съедает придыхание и слово
  звучит обрубленным;
- не является драматической паузой перед репликой (см. `KEEP_BEFORE_SPEECH`).

Из каждой найденной паузы вырезается не вся длина: остаток той же длины, что
и порог, остаётся на месте. Речь без пауз вовсе звучит неестественно.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

import numpy as np

from narezka.core.edl import Edl

#: Насколько окно должно быть тише опорного уровня речи, чтобы считаться
#: тишиной. Не абсолютные децибелы: у каждой записи свой уровень, и общий
#: порог на тихой вырезал бы речь, а на громкой не находил бы пауз.
SILENCE_DROP_DB = 30.0

#: Квантиль, по которому берётся уровень речи. Медиана здесь не годится:
#: на записи, где тишины больше половины, медиана и есть тишина, порог
#: уезжает вниз и паузы перестают находиться вовсе. Изъян нашёлся тестом
#: на записи с долгим простоем. Речь — это громкая часть, поэтому опора
#: берётся вверху распределения, но не по максимуму: один щелчок микрофона
#: не должен задавать уровень для всей записи.
SPEECH_QUANTILE = 98.0

#: Пауза короче этой не трогается вовсе: на таких держится ритм речи.
MIN_PAUSE_SECONDS = 0.7

#: Сколько паузы остаётся после вырезки. Речь встык звучит неестественно,
#: и это слышно даже тем, кто не понимает, что не так.
KEEP_PAUSE_SECONDS = 0.25

#: Отступ от границы слова. Рез вплотную съедает придыхание и хвост
#: последнего звука, отчего слово звучит обрубленным.
WORD_MARGIN_SECONDS = 0.12

#: Пауза непосредственно перед речью длиной до этой считается драматической
#: и не трогается: молчание перед репликой — часть реплики.
KEEP_BEFORE_SPEECH = 2.0


@dataclass(frozen=True)
class SilenceReport:
    edl: Edl
    #: Сколько пауз найдено и сколько из них вырезано — разница показывает,
    #: сколько сохранено осознанно, а не пропущено по ошибке.
    found: int
    cut: int
    removed_seconds: float

    def as_dict(self) -> dict[str, object]:
        return {
            "found": self.found,
            "cut": self.cut,
            "removed_seconds": round(self.removed_seconds, 2),
            "edl": self.edl.as_dict(),
        }


def _silent_windows(rms_db: np.ndarray, drop_db: float) -> np.ndarray:
    """Маска тишины относительно уровня речи в записи."""
    if rms_db.size == 0:
        return np.zeros(0, dtype=bool)
    finite = rms_db[np.isfinite(rms_db)]
    if finite.size == 0:
        return np.zeros(rms_db.size, dtype=bool)
    reference = float(np.percentile(finite, SPEECH_QUANTILE))
    return np.nan_to_num(rms_db, neginf=-120.0) < reference - drop_db


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Непрерывные участки True как пары индексов [начало, конец)."""
    if mask.size == 0:
        return []
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2], strict=True)]


def find_pauses(
    rms_db: np.ndarray,
    window_seconds: float,
    *,
    drop_db: float = SILENCE_DROP_DB,
    min_pause: float = MIN_PAUSE_SECONDS,
) -> list[tuple[float, float]]:
    """Паузы длиннее порога, в секундах исходника."""
    mask = _silent_windows(np.asarray(rms_db, dtype=np.float64), drop_db)
    pauses = []
    for begin, end in _runs(mask):
        start, finish = begin * window_seconds, end * window_seconds
        if finish - start >= min_pause:
            pauses.append((start, finish))
    return pauses


def _word_bounds(words: list[dict]) -> list[tuple[float, float]]:
    bounds = []
    for word in words:
        start, end = word.get("start"), word.get("end")
        if start is None or end is None:
            continue
        bounds.append((float(start), float(end)))
    bounds.sort()
    return bounds


def plan_cuts(
    rms_db: np.ndarray,
    window_seconds: float,
    source_duration: float,
    words: list[dict] | None = None,
    *,
    drop_db: float = SILENCE_DROP_DB,
    min_pause: float = MIN_PAUSE_SECONDS,
    keep_pause: float = KEEP_PAUSE_SECONDS,
    word_margin: float = WORD_MARGIN_SECONDS,
    keep_before_speech: float = KEEP_BEFORE_SPEECH,
) -> SilenceReport:
    """Составляет список правок: что вырезать из пауз, а что оставить.

    Слова нужны, чтобы рез не попадал вплотную к речи и чтобы отличать
    драматическую паузу перед репликой от простого молчания. Без них
    вырезка всё равно работает, просто осторожнее — только по громкости.
    """
    pauses = find_pauses(
        rms_db, window_seconds, drop_db=drop_db, min_pause=min_pause
    )
    bounds = _word_bounds(words or [])
    starts = [b[0] for b in bounds]

    cuts: list[tuple[float, float]] = []
    for start, finish in pauses:
        # Отступаем от соседних слов, чтобы не срезать придыхание.
        begin = start + word_margin
        end = finish - word_margin

        # Драматическая пауза перед репликой не трогается: молчание перед
        # ответом — часть ответа, и вырезав его, мы убиваем шутку.
        if starts:
            index = bisect.bisect_left(starts, finish)
            if index < len(starts) and starts[index] - finish <= keep_before_speech:
                if finish - start <= keep_before_speech + min_pause:
                    continue

        # Оставляем часть паузы на месте: речь встык звучит неестественно.
        end -= keep_pause
        if end - begin > 0:
            cuts.append((begin, end))

    edl = Edl.cut(source_duration, cuts) if cuts else Edl.identity()
    return SilenceReport(
        edl=edl,
        found=len(pauses),
        cut=len(cuts),
        removed_seconds=sum(end - start for start, end in cuts),
    )
