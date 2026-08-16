"""Теги звука: смех, крик, аплодисменты, музыка."""

import numpy as np
import pytest

from narezka.core import audiotags
from narezka.core.audiotags import TAGS, TagTrack, class_names


def track(**series) -> TagTrack:
    return TagTrack(hop=0.5, scores={k: np.array(v, dtype=float) for k, v in series.items()})


def test_class_indices_match_the_official_map():
    """Номера классов закреплены: их сдвиг означал бы, что «смех» молча
    превратился в другой звук, и заметить это было бы нечем."""
    names = class_names()
    expected = {
        "laughter": {"Laughter"},
        "applause": {"Cheering", "Applause"},
        "shout": {"Shout", "Screaming"},
        "crowd": {"Crowd"},
        "music": {"Music"},
    }
    for tag, indices in TAGS.items():
        assert {names[i] for i in indices} == expected[tag], f"тег {tag} указывает не туда"


def test_window_takes_the_peak_not_the_average():
    """Смех длится секунду посреди двадцати секунд речи.

    Усреднение размазало бы его до неразличимости, а нас интересует сам факт.
    """
    t = track(laughter=[0.0, 0.0, 0.9, 0.0, 0.0])
    assert t.window("laughter", 0.0, 2.5) == pytest.approx(0.9)


def test_share_measures_duration_not_peak():
    """Для музыки нужна доля: одиночный всплеск не страшен, а музыка на
    протяжении всего ролика и есть риск Content ID."""
    t = track(music=[0.9, 0.0, 0.0, 0.0])
    assert t.share("music", 0.0, 2.0) == pytest.approx(0.25)


def test_unknown_tag_is_zero_not_error():
    """Отсутствующий тег — ноль, а не падение: набор тегов настраивается."""
    assert track(music=[1.0]).window("нет-такого", 0.0, 1.0) == 0.0


def test_empty_window_is_zero():
    t = track(music=[1.0, 1.0])
    assert t.window("music", 5.0, 5.0) == 0.0


def test_window_is_clamped_to_the_track():
    """Запрос за пределами дорожки не выходит за массив."""
    t = track(music=[0.5, 0.7])
    assert t.window("music", 0.0, 1000.0) == pytest.approx(0.7)


def test_several_classes_collapse_to_the_strongest():
    """Аплодисменты и одобрительный гул — один звук с точки зрения того,
    зачем мы их ищем."""
    class FakeSession:
        def get_inputs(self):
            return [type("I", (), {"name": "waveform"})()]

        def run(self, _outputs, feed):
            scores = np.zeros((2, 521))
            scores[:, 61] = 0.2   # Cheering
            scores[:, 62] = 0.8   # Applause
            return [scores]

    tagger = audiotags.Yamnet.__new__(audiotags.Yamnet)
    tagger._session = FakeSession()
    tagger._input = "waveform"

    result = tagger.tag(np.ones(1000, dtype=np.float32))
    assert result.scores["applause"].max() == pytest.approx(0.8)


def test_empty_audio_gives_empty_tracks():
    tagger = audiotags.Yamnet.__new__(audiotags.Yamnet)
    result = tagger.tag(np.zeros(0, dtype=np.float32))
    assert result.frames == 0
    assert set(result.scores) == set(TAGS)


def test_every_tag_has_its_own_switch():
    """Каждый тег включается отдельно.

    Считать то, чем не пользуются, значит платить временем и местом за ничто:
    аплодисменты и толпа осмысленны лишь на записях с залом.
    """
    from narezka.core.config import AudioTagsConfig

    cfg = AudioTagsConfig()
    for tag in TAGS:
        assert hasattr(cfg, tag), f"у тега {tag} нет переключателя"


def test_hall_only_tags_are_off_by_default():
    """Аплодисменты и толпа выключены: на записи одного стримера их нет."""
    from narezka.core.config import AudioTagsConfig

    cfg = AudioTagsConfig()
    assert cfg.laughter and cfg.music
    assert not cfg.applause and not cfg.crowd


def test_switches_reach_the_cache_key():
    """Выбор тегов входит в ключ кэша.

    Иначе, включив музыку, пользователь получил бы прежний результат
    из кэша — молча и без объяснений.
    """
    from narezka.core.config import AudioTagsConfig
    from narezka.stages.audiotags import AudioTagsStage

    class Ctx:
        class config:
            audiotags = AudioTagsConfig()
        class paths:
            pass

    first = AudioTagsStage().config_slice(Ctx())
    Ctx.config.audiotags = AudioTagsConfig(crowd=True)
    second = AudioTagsStage().config_slice(Ctx())
    assert first != second
