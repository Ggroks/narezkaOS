"""Применение правок оси времени внутри клипа."""

import pytest

from narezka.core.cuts import build_select, clip_cuts, spans_within
from narezka.core.edl import Edl


def test_identity_needs_no_filters():
    """Правок нет — фильтры не ставятся.

    Лишний select стоил бы прохода по всем кадрам ради тождественного
    результата.
    """
    video, audio, duration = clip_cuts(Edl.identity(), 10.0, 70.0)
    assert video == "" and audio == ""
    assert duration == pytest.approx(60.0)


def test_untouched_clip_needs_no_filters():
    """Клип, которого вырезки не коснулись, тоже обходится без фильтров."""
    edl = Edl.cut(300.0, [(200.0, 210.0)])
    video, audio, duration = clip_cuts(edl, 10.0, 70.0)
    assert video == ""
    assert duration == pytest.approx(60.0)


def test_cut_inside_clip_shortens_it():
    """Вырезка внутри клипа укорачивает его, и длительность пересчитывается.

    Без пересчёта ffmpeg оборвал бы ролик раньше конца или дописал пустоту:
    он получает длительность отдельным параметром.
    """
    edl = Edl.cut(300.0, [(30.0, 35.0)])
    video, audio, duration = clip_cuts(edl, 10.0, 70.0)

    assert duration == pytest.approx(55.0)
    assert "between(t,0.000,20.000)" in video
    assert "between(t,25.000,60.000)" in video


def test_audio_filter_is_always_paired():
    """Звук отбирается своим фильтром.

    Пропустить его значит получить рассинхрон звука с картинкой — самую
    заметную из возможных ошибок.
    """
    edl = Edl.cut(300.0, [(30.0, 35.0)])
    video, audio, _ = clip_cuts(edl, 10.0, 70.0)

    assert video and audio
    assert "aselect" in audio and "asetpts" in audio


def test_timestamps_are_rebuilt():
    """Метки пересобираются, иначе на месте вырезки встанет стоп-кадр.

    select выбрасывает кадры, но оставляет их метки: без setpts ffmpeg
    честно сохранит дыру нужной длины. Ошибка тихая — файл соберётся.
    """
    edl = Edl.cut(300.0, [(30.0, 35.0)])
    video, audio, _ = clip_cuts(edl, 10.0, 70.0)

    assert "setpts=N/FRAME_RATE/TB" in video
    assert "asetpts=N/SR/TB" in audio


def test_several_cuts_in_one_clip():
    edl = Edl.cut(300.0, [(20.0, 25.0), (40.0, 42.0)])
    _, _, duration = clip_cuts(edl, 10.0, 70.0)
    assert duration == pytest.approx(53.0)


def test_clip_fully_cut_gives_nothing():
    """От клипа ничего не осталось — фильтров нет, длительность ноль."""
    edl = Edl.cut(300.0, [(10.0, 70.0)])
    video, audio, duration = clip_cuts(edl, 10.0, 70.0)

    assert video == "" and audio == ""
    assert duration == pytest.approx(0.0)


def test_pieces_are_relative_to_clip_start():
    """Куски считаются от начала клипа, а не записи.

    ffmpeg получает клип уже вырезанным по -ss, и время внутри идёт от нуля.
    """
    edl = Edl.cut(300.0, [(100.0, 110.0)])
    pieces = spans_within(edl, 90.0, 130.0)

    assert pieces[0] == pytest.approx((0.0, 10.0))
    assert pieces[1] == pytest.approx((20.0, 40.0))


def test_empty_pieces_give_empty_filters():
    assert build_select([], 10.0) == ("", "")
