"""Дешёвые сигналы по всему материалу.

BAZA.md §4 и §11: сначала дешёвые алгоритмы по всему VOD, потом дорогой AI
только по кандидатам. Всё в этом модуле должно считаться на CPU за время
порядка минут даже на восьмичасовой записи.

Модуль чистый: принимает массивы и списки, возвращает числа. Работа с файлами
и артефактами — в стадии.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class LoudnessTrack:
    """Огибающая громкости по окнам фиксированной длины."""

    window_seconds: float
    #: Среднеквадратичный уровень в дБ относительно полной шкалы.
    rms_db: np.ndarray

    @property
    def count(self) -> int:
        return len(self.rms_db)

    def time_of(self, index: int) -> float:
        return index * self.window_seconds


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    """Читает 16-битный PCM WAV в массив float32 от -1 до 1.

    Стадия extract_audio всегда пишет именно такой формат, поэтому обходимся
    стандартной библиотекой — лишняя зависимость ради одного чтения не нужна.
    """
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise ValueError(f"ожидается 16-битный PCM, получено {handle.getsampwidth() * 8} бит")
        channels = handle.getnchannels()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def loudness_track(samples: np.ndarray, rate: int, window_seconds: float = 1.0) -> LoudnessTrack:
    """Среднеквадратичный уровень по окнам, в дБ."""
    window = max(int(rate * window_seconds), 1)
    usable = (len(samples) // window) * window
    if usable == 0:
        return LoudnessTrack(window_seconds, np.zeros(0, dtype=np.float32))

    frames = samples[:usable].reshape(-1, window)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    # Порог отсечки: тишина не должна уходить в минус бесконечность.
    rms_db = 20.0 * np.log10(np.maximum(rms, 1e-6))
    return LoudnessTrack(window_seconds, rms_db.astype(np.float32))


def robust_z(values: np.ndarray) -> np.ndarray:
    """Отклонение от медианы в единицах MAD.

    Медиана и MAD вместо среднего и дисперсии: на стриме есть выбросы —
    крики, музыкальные вставки, — и они не должны сдвигать точку отсчёта,
    относительно которой ищутся всплески.
    """
    if len(values) == 0:
        return values
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad >= 1e-9:
        # 1.4826 приводит MAD к масштабу стандартного отклонения
        # для нормального распределения.
        return (values - median) / (mad * 1.4826)

    # MAD равен нулю, когда больше половины значений совпадают: цифровая
    # тишина, ровный тон, длинная пауза. Настоящий выброс при этом
    # существует, и обнулять его нельзя — переходим на разброс.
    spread = float(np.std(values))
    if spread < 1e-9:
        return np.zeros_like(values)
    return (values - median) / spread


def speech_density(
    segments: list[dict],
    total_windows: int,
    window_seconds: float,
) -> np.ndarray:
    """Слов в секунду по тем же окнам, что и громкость.

    Плотная речь — признак того, что человек увлечён происходящим; молчание
    посреди записи чаще всего означает загрузку, меню или AFK (§16).
    """
    density = np.zeros(total_windows, dtype=np.float32)
    for segment in segments:
        words = segment.get("words") or []
        if not words:
            continue
        for word in words:
            index = int(word["start"] / window_seconds)
            if 0 <= index < total_windows:
                density[index] += 1.0
    return density / window_seconds


def find_peaks(score: np.ndarray, *, min_score: float, min_gap: int) -> list[int]:
    """Локальные максимумы выше порога, разнесённые минимум на min_gap окон.

    Разнесение нужно, чтобы один длинный всплеск не породил десяток
    кандидатов вокруг одного и того же события.
    """
    if len(score) == 0:
        return []

    order = np.argsort(score)[::-1]
    chosen: list[int] = []
    for index in order:
        value = float(score[index])
        if value < min_score:
            break
        if all(abs(int(index) - other) >= min_gap for other in chosen):
            chosen.append(int(index))
    return sorted(chosen)


def snap_to_segments(
    start: float,
    end: float,
    segments: list[dict],
) -> tuple[float, float] | None:
    """Расширяет интервал до границ ближайших сегментов речи.

    §14 и §15: клип не должен начинаться и заканчиваться посреди фразы.
    Это грубое приближение — окончательные границы ищет LLM по смыслу,
    но даже такое выравнивание убирает обрывы на полуслове.
    """
    inside = [s for s in segments if s["end"] > start and s["start"] < end]
    if not inside:
        return None
    return min(s["start"] for s in inside), max(s["end"] for s in inside)


def overlap_ratio(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Доля перекрытия относительно более короткого интервала."""
    left = max(a[0], b[0])
    right = min(a[1], b[1])
    if right <= left:
        return 0.0
    shorter = min(a[1] - a[0], b[1] - b[0])
    return (right - left) / shorter if shorter > 0 else 0.0


def limit_coverage(
    candidates: list[dict],
    *,
    total_seconds: float,
    max_coverage: float,
) -> tuple[list[dict], bool]:
    """Оставляет сильнейших кандидатов в пределах доли материала.

    Оценка всплеска считается относительно самого материала, поэтому на
    однородной записи — ровном повествовании, монотонном геймплее — «пики»
    находятся там, где событий нет. Без потолка воронка вернула бы половину
    записи и создала видимость отбора.

    Второй элемент результата — упёрлись ли в потолок. Это сигнал, что
    различающего признака в материале не нашлось, и его стоит показать,
    а не проглотить (§54).
    """
    budget = total_seconds * max_coverage
    kept: list[dict] = []
    used = 0.0
    for candidate in sorted(candidates, key=lambda c: c["provisional_score"], reverse=True):
        duration = candidate["end"] - candidate["start"]
        if used + duration > budget and kept:
            return sorted(kept, key=lambda c: c["start"]), True
        kept.append(candidate)
        used += duration
    return sorted(kept, key=lambda c: c["start"]), False


def deduplicate(
    candidates: list[dict],
    *,
    max_overlap: float,
) -> list[dict]:
    """Отбрасывает кандидатов, сильно перекрывающихся с более сильными.

    Правило заимствовано из исходного проекта (docs/upstream-notes.md):
    сортировка по убыванию оценки, отбрасывание при перекрытии выше порога.
    """
    kept: list[dict] = []
    for candidate in sorted(candidates, key=lambda c: c["provisional_score"], reverse=True):
        span = (candidate["start"], candidate["end"])
        if any(overlap_ratio(span, (k["start"], k["end"])) > max_overlap for k in kept):
            continue
        kept.append(candidate)
    return sorted(kept, key=lambda c: c["start"])


#: Символы, которыми зритель реагирует, а не разговаривает. Сюда попадают
#: эмодзи, но главное — не они: на Twitch реакция идёт смайлами канала
#: (KEKW, PogChamp, Sadge) и повторами одного слова. Список общих ловит
#: базовый случай, остальное берёт правило повтора.
REACTION_WORDS = frozenset({
    "kekw", "lul", "lmao", "pog", "pogchamp", "poggers", "monkas", "sadge",
    "omegalul", "ez", "gg", "f", "ахах", "ахахах", "хаха", "лол", "кек", "жиза",
})

#: Со скольких одинаковых сообщений подряд считать это волной. Двух мало:
#: совпадения случаются. Три — уже согласие зала.
WAVE_REPEATS = 3


def _is_reaction(text: str) -> bool:
    """Сообщение — реакция, а не разговор.

    Реакция короткая и состоит из смайлов, повторов или междометий. Отличать
    её от обычной реплики полезно потому, что сто сообщений «согласен» и сто
    «KEKW» означают разное: первое — обсуждение, второе — момент смешной.
    """
    stripped = text.strip().lower()
    if not stripped or len(stripped) > 40:
        return False
    words = stripped.split()
    if not words:
        return False
    # Все слова из словаря реакций либо одно слово повторено несколько раз.
    if all(w.strip("!?.,") in REACTION_WORDS for w in words):
        return True
    return len(words) >= 2 and len(set(words)) == 1


def chat_reaction_share(
    messages: list[dict],
    window_count: int,
    window_seconds: float,
    *,
    skip_bots: bool = True,
) -> np.ndarray:
    """Доля реакций в чате по окнам — отдельный признак от плотности.

    BAZA.md §41. Плотность отвечает на вопрос «много ли пишут», а доля
    реакций — «пишут ли они в ответ на происходящее». Спор в чате даёт
    высокую плотность при нулевой доле реакций, а удачная шутка — наоборот:
    сообщений может быть немного, но почти все они смайлы.

    Возвращается доля, а не количество: количество уже учтено плотностью,
    и складывать два признака, меняющихся вместе, значит считать один дважды.
    """
    totals = np.zeros(window_count, dtype=np.float64)
    reactions = np.zeros(window_count, dtype=np.float64)
    if window_count <= 0 or window_seconds <= 0:
        return reactions

    runs: dict[int, tuple[str, int]] = {}
    for message in messages:
        if skip_bots and message.get("is_bot"):
            continue
        at = message.get("at")
        if at is None:
            continue
        index = int(at / window_seconds)
        if not 0 <= index < window_count:
            continue
        text = str(message.get("text", ""))
        totals[index] += 1

        # Волна одинаковых сообщений засчитывается как реакция целиком:
        # когда зал повторяет одно и то же, это и есть реакция, даже если
        # само слово в словарь не попало.
        previous, count = runs.get(index, ("", 0))
        normalized = text.strip().lower()
        count = count + 1 if normalized == previous else 1
        runs[index] = (normalized, count)

        if _is_reaction(text) or count >= WAVE_REPEATS:
            reactions[index] += 1

    with np.errstate(invalid="ignore", divide="ignore"):
        share = np.where(totals > 0, reactions / totals, 0.0)
    return share


def chat_rate(
    messages: list[dict],
    window_count: int,
    window_seconds: float,
    *,
    skip_bots: bool = True,
) -> np.ndarray:
    """Плотность сообщений чата по тем же окнам, что и громкость.

    BAZA.md §41. Всплеск чата — прямое свидетельство того, что зрители сочли
    момент важным, и он не зависит от громкости: тихая, но неожиданная сцена
    даёт всплеск сообщений при ровном звуке.

    Сообщения ботов по умолчанию не считаются: они идут по расписанию,
    а не в ответ на происходящее, и создают всплески на пустом месте.
    """
    rate = np.zeros(window_count, dtype=np.float64)
    if window_count <= 0 or window_seconds <= 0:
        return rate

    for message in messages:
        if skip_bots and message.get("is_bot"):
            continue
        at = message.get("at")
        if at is None:
            continue
        index = int(float(at) / window_seconds)
        if 0 <= index < window_count:
            rate[index] += 1.0

    return rate / window_seconds


def smooth(values: np.ndarray, window: int) -> np.ndarray:
    """Скользящее среднее по окну.

    Нужно чату: зрители реагируют с запозданием и вразнобой, поэтому всплеск
    размазан на несколько секунд. Без сглаживания он не виден как единый
    всплеск, а рассыпается на дрожь вокруг фона.
    """
    if window <= 1 or values.size == 0:
        return values
    kernel = np.ones(min(window, values.size)) / min(window, values.size)
    return np.convolve(values, kernel, mode="same")


def forward_average(values: np.ndarray, window: int) -> np.ndarray:
    """Среднее по окну **вперёд**: значение в точке t — это среднее [t, t+window].

    Нужно чату. Замер на записи стрима (усреднение по 10 всплескам громкости)
    показал, что реакция зрителей — не сдвиг на пару секунд, а растянутое
    плато: чат поднимается сразу от события и держится повышенным ещё
    15–20 секунд, а до события он на уровне фона или ниже.

    Поэтому симметричное сглаживание неверно: оно подмешивает в оценку момента
    чат, который к нему не относится, и размазывает всплеск назад по времени —
    кандидат начинается раньше, чем произошло событие. Окно вперёд приписывает
    реакцию её причине.
    """
    if window <= 1 or values.size == 0:
        return values

    window = min(window, values.size)
    # Накопленная сумма даёт скользящее окно за один проход независимо
    # от его ширины — на восьмичасовой записи это заметно.
    padded = np.concatenate([values, np.zeros(window)])
    cumulative = np.concatenate([[0.0], np.cumsum(padded)])
    counts = np.minimum(np.arange(values.size) + window, values.size) - np.arange(values.size)
    return (cumulative[np.arange(values.size) + window] - cumulative[: values.size]) / counts
