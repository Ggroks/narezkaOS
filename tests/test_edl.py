"""Единая ось времени.

Проверяются не только примеры, но и свойства: пересчёт должен быть монотонен
и обратим для любого сохранённого момента, иначе субтитры разъедутся на
записи, которую никто не догадался проверить руками.
"""

import random

import pytest

from narezka.core.edl import EPS, Edl, EdlError


def test_identity_changes_nothing():
    """Без правок ось тождественна — это рабочий случай, не вырожденный."""
    edl = Edl.identity()
    assert edl.is_identity
    assert edl.to_output(12.5) == 12.5
    assert edl.to_source(12.5) == 12.5


def test_cut_removes_middle():
    """Вырезка в середине сдвигает всё, что после неё."""
    edl = Edl.cut(100.0, [(30.0, 40.0)])

    assert edl.output_duration == pytest.approx(90.0)
    assert edl.to_output(10.0) == pytest.approx(10.0)
    assert edl.to_output(50.0) == pytest.approx(40.0), "после вырезки время сдвинулось на 10 с"
    assert edl.to_source(40.0) == pytest.approx(50.0)


def test_time_inside_a_cut_is_none():
    """Вырезанный момент возвращает None, а не ближайшую границу.

    Подмена молчаливо сдвинула бы субтитр в чужое место, и заметили бы это
    только глазами на готовом ролике.
    """
    edl = Edl.cut(100.0, [(30.0, 40.0)])
    assert edl.to_output(35.0) is None


def test_boundaries_belong_to_kept_part():
    """Границы вырезки принадлежат сохранённой части, а не пропадают."""
    edl = Edl.cut(100.0, [(30.0, 40.0)])
    assert edl.to_output(30.0) == pytest.approx(30.0)
    assert edl.to_output(40.0) == pytest.approx(30.0)


def test_overlapping_cuts_merge():
    """Два детектора могут указать на один участок — это не ошибка."""
    edl = Edl.cut(100.0, [(30.0, 45.0), (40.0, 50.0)])
    assert edl.output_duration == pytest.approx(80.0)


def test_touching_cuts_merge():
    edl = Edl.cut(100.0, [(10.0, 20.0), (20.0, 30.0)])
    assert edl.output_duration == pytest.approx(80.0)


def test_overlapping_kept_spans_are_rejected():
    """Перехлёст сохранённых отрезков — ошибка выше по течению.

    Чинить его здесь значит спрятать до момента, когда разъедутся субтитры.
    """
    with pytest.raises(EdlError, match="перекрываются"):
        Edl.keep([(0.0, 50.0), (40.0, 60.0)])


def test_cuts_at_the_edges():
    """Вырезка у самого начала и конца не оставляет пустых отрезков."""
    edl = Edl.cut(100.0, [(0.0, 10.0), (90.0, 100.0)])
    assert edl.output_duration == pytest.approx(80.0)
    assert edl.to_output(10.0) == pytest.approx(0.0)
    assert edl.to_output(95.0) is None


def test_everything_cut():
    """Вырезано всё — длительность ноль, и пересчёт не падает."""
    edl = Edl.cut(50.0, [(0.0, 50.0)])
    assert edl.output_duration == pytest.approx(0.0)
    assert edl.to_output(25.0) is None


def test_round_trip_holds_for_every_kept_moment():
    """Свойство: to_source(to_output(t)) == t для любого сохранённого t.

    Проверяется на случайных, но воспроизводимых наборах правок: примеры
    ловят придуманные случаи, свойство — непридуманные.
    """
    rng = random.Random(20260816)
    for _ in range(200):
        duration = rng.uniform(30.0, 600.0)
        cuts = []
        position = 0.0
        while position < duration:
            position += rng.uniform(1.0, 30.0)
            length = rng.uniform(0.5, 10.0)
            if position + length < duration:
                cuts.append((position, position + length))
            position += length
        edl = Edl.cut(duration, cuts)

        for _ in range(20):
            t = rng.uniform(0.0, duration)
            out = edl.to_output(t)
            if out is None:
                continue
            assert edl.to_source(out) == pytest.approx(t, abs=1e-4)


def test_output_is_monotonic():
    """Свойство: больший момент исходника не станет меньшим на выходе."""
    rng = random.Random(7)
    edl = Edl.cut(300.0, [(20.0, 35.0), (100.0, 140.0), (200.0, 205.0)])
    previous = -1.0
    for t in sorted(rng.uniform(0.0, 300.0) for _ in range(500)):
        out = edl.to_output(t)
        if out is None:
            continue
        assert out >= previous - EPS
        previous = out


def test_span_shift_narrows_to_kept_part():
    """Отрезок, задевший вырезку, сужается, а не выбрасывается."""
    edl = Edl.cut(100.0, [(30.0, 40.0)])
    assert edl.shift_span(25.0, 45.0) == pytest.approx((25.0, 35.0))


def test_span_fully_inside_a_cut_is_none():
    edl = Edl.cut(100.0, [(30.0, 40.0)])
    assert edl.shift_span(32.0, 38.0) is None


def test_survives_serialisation():
    """Список правок переживает запись в артефакт и чтение обратно."""
    edl = Edl.cut(100.0, [(30.0, 40.0), (70.0, 75.0)])
    restored = Edl.from_dict(edl.as_dict())
    assert restored.output_duration == pytest.approx(edl.output_duration)
    assert restored.to_output(80.0) == pytest.approx(edl.to_output(80.0))


def test_removed_reports_cut_seconds():
    edl = Edl.cut(100.0, [(30.0, 40.0)])
    assert edl.removed(100.0) == pytest.approx(10.0)


def test_identity_survives_serialisation():
    """Тождественность переживает запись и чтение.

    Без явного признака оба состояния — «правок нет» и «вырезано всё» —
    дают пустой список отрезков, и на границе сериализации тот же изъян
    воспроизводился заново.
    """
    restored = Edl.from_dict(Edl.identity().as_dict())
    assert restored.is_identity
    assert restored.to_output(42.0) == 42.0


def test_fully_cut_survives_serialisation():
    """И обратное состояние тоже: вырезано всё — значит вырезано всё."""
    restored = Edl.from_dict(Edl.cut(50.0, [(0.0, 50.0)]).as_dict())
    assert not restored.is_identity
    assert restored.to_output(25.0) is None
