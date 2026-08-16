"""Теги звука: смех, крик, аплодисменты, музыка.

BAZA.md §10, §43. Закрывает то, чего не даёт ни один текущий сигнал: сейчас
смех ловится только косвенно, через всплеск громкости, и неотличим от крика,
удара или щелчка микрофона.

**Модель.** YAMNet — свёрточная сеть Google на AudioSet, 521 класс, вход
16 кГц моно (ровно наш формат после `extract_audio`), окно 0.96 с с шагом
0.48 с. Веса под Apache-2.0. Запускается через onnxruntime, без torch.

**Зачем именно классы, а не «громко/тихо».** Всплеск громкости отвечает на
вопрос «что-то произошло», а тег — «что именно». Это разные сведения: смех
зрителей и выстрел в игре дают одинаковый всплеск, но первый означает удачный
момент, а второй — рядовой геймплей.

**Музыка — единственный тег со знаком минус.** Ролик с фоновой музыкой ловит
Content ID, теряет монетизацию или блокируется, поэтому её присутствие
работает штрафом, а не признаком (§54).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "models"
MODEL_NAME = "yamnet.onnx"
CLASSES_NAME = "yamnet_classes.csv"

#: Частота, на которой обучен YAMNet. Совпадает с той, в которой мы уже
#: держим звук, поэтому пересчёт не нужен — а он был бы худшим местом:
#: некачественная передискретизация смещает спектр и портит распознавание.
SAMPLE_RATE = 16000

#: Шаг кадров модели, секунды. Нужен, чтобы перевести номер кадра во время.
FRAME_HOP = 0.48

#: Интересующие нас классы AudioSet и их роль. Номера проверены по карте
#: классов из репозитория TensorFlow и закреплены тестом: их сдвиг означал бы,
#: что «смех» молча превратился в другой звук.
TAGS: dict[str, tuple[int, ...]] = {
    # Смех — сильнейший признак удачного момента на стриме.
    "laughter": (13,),
    # Аплодисменты и одобрительный гул зрителей.
    "applause": (61, 62),
    # Крик: и восторг, и испуг. Сам по себе неоднозначен, но в паре
    # с чатом различается.
    "shout": (6, 11),
    # Толпа — фон стадиона или зала, отличает публичное событие от студии.
    "crowd": (64,),
    # Музыка: единственный тег со знаком минус, риск Content ID.
    "music": (132,),
}

#: Ниже этой уверенности тег не засчитывается. YAMNet выдаёт вероятность на
#: каждый из 521 класса, и слабые срабатывания есть почти в каждом кадре.
MIN_SCORE = 0.3


class AudioTagError(RuntimeError):
    """Разметка звука недоступна."""


@dataclass(frozen=True)
class TagTrack:
    """Дорожки тегов по кадрам модели."""

    hop: float
    #: Имя тега → вероятность в каждом кадре.
    scores: dict[str, np.ndarray]

    @property
    def frames(self) -> int:
        return len(next(iter(self.scores.values()))) if self.scores else 0

    def window(self, name: str, start: float, end: float) -> float:
        """Максимум тега на отрезке. Максимум, а не среднее.

        Смех длится секунду посреди двадцати секунд речи: усреднение
        размазало бы его до неразличимости, а нас интересует сам факт.
        """
        track = self.scores.get(name)
        if track is None or track.size == 0:
            return 0.0
        a = max(0, int(start / self.hop))
        b = min(track.size, int(np.ceil(end / self.hop)))
        return float(track[a:b].max()) if b > a else 0.0

    def share(self, name: str, start: float, end: float, threshold: float = MIN_SCORE) -> float:
        """Доля кадров отрезка, где тег выше порога.

        Для музыки нужна именно доля: одиночный всплеск не страшен, а вот
        музыка на протяжении всего ролика и есть риск Content ID.
        """
        track = self.scores.get(name)
        if track is None or track.size == 0:
            return 0.0
        a = max(0, int(start / self.hop))
        b = min(track.size, int(np.ceil(end / self.hop)))
        if b <= a:
            return 0.0
        return float((track[a:b] >= threshold).mean())


def available() -> str | None:
    """Причина недоступности. None — можно размечать."""
    import importlib.util  # noqa: PLC0415

    if importlib.util.find_spec("onnxruntime") is None:
        return "модуль onnxruntime не установлен"
    for name in (MODEL_NAME, CLASSES_NAME):
        if not (MODELS_DIR / name).is_file():
            return f"нет файла {name} — положите его в {MODELS_DIR}"
    return None


def class_names() -> list[str]:
    """Названия классов AudioSet в порядке выходов модели."""
    with (MODELS_DIR / CLASSES_NAME).open(encoding="utf-8") as handle:
        return [row["display_name"] for row in csv.DictReader(handle)]


class Yamnet:
    """Разметка звука YAMNet. Создаётся один раз: загрузка тяжелее прогона."""

    def __init__(self, model: str = MODEL_NAME) -> None:
        reason = available()
        if reason:
            raise AudioTagError(reason)
        import onnxruntime  # noqa: PLC0415

        self._session = onnxruntime.InferenceSession(
            str(MODELS_DIR / model), providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name

    def tag(self, samples: np.ndarray, tags: dict[str, tuple[int, ...]] | None = None) -> TagTrack:
        """Размечает волну. Ожидается моно 16 кГц в диапазоне [-1, 1]."""
        wanted = tags or TAGS
        if samples.size == 0:
            return TagTrack(FRAME_HOP, {name: np.zeros(0) for name in wanted})

        scores = self._session.run(None, {self._input: samples.astype(np.float32)})[0]
        return TagTrack(
            FRAME_HOP,
            {
                # Из нескольких номеров берётся максимум: «аплодисменты» и
                # «одобрительный гул» — один и тот же звук с точки зрения
                # того, зачем мы их ищем.
                name: scores[:, list(indices)].max(axis=1)
                for name, indices in wanted.items()
            },
        )
