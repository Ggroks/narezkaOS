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
