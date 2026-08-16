"""Единая ось времени: список правок и пересчёт между исходником и выходом.

BAZA.md §16, §44. Вводится **до** первого удаления тишины, а не после.

**Зачем.** Любая вырезка сдвигает всё, что идёт после неё. Субтитры, границы
слов, траектория кадрирования и главы описания считаются в исходном времени;
готовый ролик живёт в выходном. Без общего списка правок и пары функций
пересчёта рассинхрон субтитров не вероятен, а неизбежен — и проявится не
сразу, а на первой записи, где тишину действительно вырезали.

**Устройство.** Список правок — это упорядоченные непересекающиеся отрезки
исходника, которые остаются. Всё, что между ними, вырезано. Такое
представление выбрано вместо списка вырезок потому, что пересчёт времени
по нему — прямой поиск по накопленным длительностям, а не вычитание с
накоплением ошибки.

**Что гарантируется:**

- пересчёт монотонен: больший момент исходника не станет меньшим на выходе;
- обратный пересчёт точен для любого сохранённого момента (`to_source` от
  `to_output` возвращает исходное значение);
- момент внутри вырезанного участка честно возвращает None, а не ближайшую
  границу: подмена молчаливо сдвинула бы субтитр в чужое место.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any

#: Допуск сравнения времён, секунды. Времена приходят из ffmpeg и whisper
#: в виде float, и точное равенство границ проверять нельзя.
EPS = 1e-6


class EdlError(ValueError):
    """Список правок противоречив."""


@dataclass(frozen=True)
class Span:
    """Отрезок исходника, который остаётся в выходе."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def as_dict(self) -> dict[str, float]:
        return {"start": round(float(self.start), 6), "end": round(float(self.end), 6)}


@dataclass(frozen=True)
class Edl:
    """Список правок: что осталось от исходника и в каком порядке.

    `Edl.identity()` — правок нет, пересчёт тождественный. Это не вырожденный
    случай, а рабочий: пока тишина не удаляется, ось времени уже подключена,
    и весь остальной код пишется одинаково.

    Пустой набор отрезков без признака тождественности означает обратное —
    вырезано всё.
    """

    spans: tuple[Span, ...] = ()

    #: Отличает «правок нет» от «вырезано всё». Без этого признака пустой
    #: список отрезков означал бы оба состояния сразу, а они противоположны:
    #: в первом любой момент проходит насквозь, во втором не проходит ни один.
    #: Изъян нашёлся тестом на полностью вырезанную запись.
    identity_flag: bool = False

    #: Накопленные длительности перед каждым отрезком — начала отрезков в
    #: выходном времени. Считаются один раз: пересчёт вызывается на каждое
    #: слово субтитров, а их десятки тысяч.
    _offsets: tuple[float, ...] = ()

    @staticmethod
    def identity() -> "Edl":
        """Правок нет: выходное время равно исходному."""
        return Edl(identity_flag=True)

    @staticmethod
    def keep(spans: list[tuple[float, float]]) -> "Edl":
        """Собирает список правок из отрезков, которые нужно сохранить.

        Отрезки проверяются, а не чинятся молча: перехлёст означает ошибку
        выше по течению, и починка здесь спрятала бы её до момента, когда
        разъедутся субтитры.
        """
        cleaned: list[Span] = []
        for start, end in spans:
            if end - start <= EPS:
                # Пустой отрезок не ошибка: он получается при вырезке,
                # прилегающей к границе. Просто ничего не добавляет.
                continue
            cleaned.append(Span(float(start), float(end)))

        cleaned.sort(key=lambda s: s.start)
        for previous, current in zip(cleaned, cleaned[1:], strict=False):
            if current.start < previous.end - EPS:
                raise EdlError(
                    f"отрезки перекрываются: [{previous.start:.3f}, {previous.end:.3f}] "
                    f"и [{current.start:.3f}, {current.end:.3f}]"
                )

        offsets: list[float] = []
        total = 0.0
        for span in cleaned:
            offsets.append(total)
            total += span.duration
        # Только именованные: позиционный вызов уже один раз отправил
        # смещения в поле-признак, и тесты поймали это как семь падений.
        return Edl(spans=tuple(cleaned), _offsets=tuple(offsets))

    @staticmethod
    def cut(source_duration: float, cuts: list[tuple[float, float]]) -> "Edl":
        """Собирает список правок из вырезаемых участков.

        Удобнее там, где известно, что удаляется: тишина, пауза, реклама.
        Перекрывающиеся и соприкасающиеся вырезки объединяются — здесь это
        не сокрытие ошибки, а нормальный случай: два детектора могут указать
        на один и тот же участок.
        """
        if source_duration <= 0:
            return Edl.identity()

        ordered = sorted(
            (max(0.0, float(a)), min(float(source_duration), float(b))) for a, b in cuts
        )
        merged: list[list[float]] = []
        for start, end in ordered:
            if end - start <= EPS:
                continue
            if merged and start <= merged[-1][1] + EPS:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])

        kept: list[tuple[float, float]] = []
        position = 0.0
        for start, end in merged:
            if start - position > EPS:
                kept.append((position, start))
            position = end
        if source_duration - position > EPS:
            kept.append((position, source_duration))
        return Edl.keep(kept)

    @property
    def output_duration(self) -> float:
        """Длительность результата после всех правок."""
        return sum(span.duration for span in self.spans)

    @property
    def is_identity(self) -> bool:
        """Правок нет. Не то же самое, что «не осталось ничего»."""
        return self.identity_flag

    def removed(self, source_duration: float) -> float:
        """Сколько секунд исходника вырезано."""
        if self.is_identity:
            return 0.0
        return max(0.0, source_duration - self.output_duration)

    def to_output(self, source_time: float) -> float | None:
        """Момент исходника в выходном времени. None — момент вырезан.

        None возвращается намеренно, а не ближайшая граница: подмена
        молчаливо сдвинула бы субтитр в чужое место, и заметили бы это
        только глазами на готовом ролике.
        """
        if self.is_identity:
            return float(source_time)

        # Поиск отрезка, в который попадает момент. Список отсортирован,
        # поэтому двоичный поиск: функция вызывается на каждое слово.
        index = bisect.bisect_right([s.start for s in self.spans], source_time + EPS) - 1
        if index < 0:
            return None
        span = self.spans[index]
        if source_time > span.end + EPS:
            return None
        # Момент прижимается к границам своего отрезка: из-за EPS он может
        # оказаться на волосок снаружи, и без прижатия выходное время
        # выскочило бы за пределы отрезка.
        clamped = min(max(source_time, span.start), span.end)
        return self._offsets[index] + (clamped - span.start)

    def to_source(self, output_time: float) -> float:
        """Момент выхода в исходном времени.

        Обратное преобразование однозначно всегда: каждый момент выхода
        происходит ровно из одного момента исходника.
        """
        if self.is_identity:
            return float(output_time)
        if not self.spans:
            # Вырезано всё: выходного времени не существует, и любое
            # значение честнее отобразить в начало, чем выдумать точку.
            return 0.0

        total = self.output_duration
        clamped = min(max(float(output_time), 0.0), total)
        index = bisect.bisect_right(self._offsets, clamped + EPS) - 1
        index = min(max(index, 0), len(self.spans) - 1)
        span = self.spans[index]
        inside = clamped - self._offsets[index]
        return min(span.start + inside, span.end)

    def shift_span(self, start: float, end: float) -> tuple[float, float] | None:
        """Переносит отрезок исходника в выходное время.

        Границы, попавшие в вырезанное, притягиваются к ближайшему
        сохранённому краю — здесь это правильно: отрезок задаёт диапазон,
        и сузить его честнее, чем выбросить целиком. Если от отрезка ничего
        не осталось, возвращается None.
        """
        if self.is_identity:
            return float(start), float(end)

        pieces = [
            (max(span.start, start), min(span.end, end))
            for span in self.spans
            if min(span.end, end) - max(span.start, start) > EPS
        ]
        if not pieces:
            return None

        first = self.to_output(pieces[0][0])
        last = self.to_output(pieces[-1][1])
        if first is None or last is None:
            return None
        return first, last

    def as_dict(self) -> dict[str, Any]:
        # Признак тождественности пишется явно. Без него запись и чтение
        # превращали «правок нет» в «вырезано всё»: оба состояния дают
        # пустой список отрезков. Это тот же изъян, что нашёлся в пересчёте,
        # и на границе сериализации он воспроизводился заново.
        return {
            "identity": self.identity_flag,
            "spans": [span.as_dict() for span in self.spans],
            "output_duration": round(self.output_duration, 3),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Edl":
        if data.get("identity"):
            return Edl.identity()
        return Edl.keep([(s["start"], s["end"]) for s in data.get("spans", [])])
