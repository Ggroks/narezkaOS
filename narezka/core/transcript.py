"""Отсев галлюцинаций распознавания.

BAZA.md §57. На музыке, игровом звуке и длинной тишине Whisper устойчиво
выдумывает текст: титры, «спасибо за просмотр», зацикленные фразы. На стриме,
где почти всегда играет музыка и звучит игра, это не редкий случай, а норма.
Ложный транскрипт напрямую отравляет отбор моментов (§11).

Сегменты **помечаются, а не удаляются**: удаление затрудняет отладку, а по
метке видно, что именно и почему отброшено.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Типовые галлюцинации Whisper на русском и английском. Сравнение идёт по
#: нормализованному тексту, поэтому регистр и пунктуация не важны.
KNOWN_HALLUCINATIONS = (
    "субтитры сделал dimatorzok",
    "субтитры создавал dimatorzok",
    "субтитры делал dimatorzok",
    "редактор субтитров",
    "корректор",
    "продолжение следует",
    "спасибо за просмотр",
    "спасибо за внимание",
    "подписывайтесь на канал",
    "ставьте лайки и подписывайтесь",
    "субтитры и перевод",
    "thanks for watching",
    "thank you for watching",
    "subscribe to my channel",
    "please subscribe",
    "see you next time",
    "the end",
)

#: Маркеры музыки, которые модель ставит вместо текста.
MUSIC_MARKERS = ("♪", "♫", "[музыка]", "[music]", "(музыка)", "(music)")

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _SPACES.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


@dataclass(frozen=True)
class Verdict:
    suspect: bool
    reason: str = ""


@dataclass(frozen=True)
class RepeatInfo:
    #: Сколько раз подряд повторяется самая длинная зацикленная фраза.
    repeats: int
    #: Сколько слов сегмента она покрывает.
    span: int


#: Столько повторов подряд в живой речи не встречается — это петля модели.
CERTAIN_LOOP_REPEATS = 5

#: При меньшем числе повторов признаком служит доля сегмента: настоящая петля
#: занимает его целиком, а эмоциональный повтор вкраплён в обычную речь.
LOOP_DOMINANCE = 0.6


def find_repeat(words: list[str], max_n: int = 5) -> RepeatInfo:
    """Ищет самый длинный последовательный повтор фразы.

    Зацикливание — характерный признак галлюцинации: модель попадает в петлю
    и выдаёт одно и то же до конца окна. Но повтор сам по себе уликой не
    является: «да, да, да!» и звукоподражания — обычная живая речь, и в стриме
    это ровно те моменты, которые мы ищем. Поэтому важен не только счётчик,
    но и то, какую часть сегмента повтор занимает.
    """
    best = RepeatInfo(1, 0)
    for n in range(1, max_n + 1):
        if len(words) < n * 2:
            break
        index = 0
        while index + n <= len(words):
            phrase = words[index : index + n]
            repeats = 1
            probe = index + n
            while probe + n <= len(words) and words[probe : probe + n] == phrase:
                repeats += 1
                probe += n
            if repeats > best.repeats:
                best = RepeatInfo(repeats, repeats * n)
            index += 1 if repeats == 1 else repeats * n
    return best


def is_loop(words: list[str]) -> bool:
    """Отличает петлю модели от естественного повтора в речи."""
    info = find_repeat(words)
    if info.repeats >= CERTAIN_LOOP_REPEATS:
        return True
    if info.repeats < 3 or not words:
        return False
    return info.span / len(words) >= LOOP_DOMINANCE


def check_segment(
    segment: dict[str, Any],
    *,
    max_no_speech_prob: float,
    min_avg_logprob: float,
    max_repeat_ratio: float,
) -> Verdict:
    """Проверяет один сегмент. Порядок проверок — от самых надёжных признаков."""
    text = (segment.get("text") or "").strip()

    if not text:
        return Verdict(True, "пустой текст")

    # Регистр важен: модель пишет и «[Music]», и «[music]».
    lowered = text.lower()
    if any(marker in lowered for marker in MUSIC_MARKERS):
        return Verdict(True, "маркер музыки вместо речи")

    normalized = normalize(text)
    if not normalized:
        return Verdict(True, "текст без слов")

    for phrase in KNOWN_HALLUCINATIONS:
        if normalized == phrase or normalized.startswith(phrase + " ") or normalized.endswith(" " + phrase):
            return Verdict(True, f"типовая галлюцинация: «{phrase}»")

    no_speech = segment.get("no_speech_prob")
    if no_speech is not None and no_speech > max_no_speech_prob:
        return Verdict(True, f"вероятность отсутствия речи {no_speech:.2f}")

    avg_logprob = segment.get("avg_logprob")
    if avg_logprob is not None and avg_logprob < min_avg_logprob:
        return Verdict(True, f"низкая уверенность модели {avg_logprob:.2f}")

    words = normalized.split()
    if is_loop(words):
        return Verdict(True, "зацикленный повтор фразы")

    if len(words) >= 8:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < (1.0 - max_repeat_ratio):
            return Verdict(True, f"мало уникальных слов ({unique_ratio:.2f})")

    return Verdict(False)


def mark_suspect_segments(
    segments: list[dict[str, Any]],
    *,
    max_no_speech_prob: float = 0.6,
    min_avg_logprob: float = -1.0,
    max_repeat_ratio: float = 0.5,
) -> list[dict[str, Any]]:
    """Проставляет `suspect` и `suspect_reason`. Возвращает тот же список.

    Дополнительно ловит повтор одного и того же текста в идущих подряд
    сегментах — межсегментное зацикливание, которое внутри одного сегмента
    не видно.
    """
    previous_normalized: str | None = None
    repeat_run = 0

    for segment in segments:
        verdict = check_segment(
            segment,
            max_no_speech_prob=max_no_speech_prob,
            min_avg_logprob=min_avg_logprob,
            max_repeat_ratio=max_repeat_ratio,
        )

        current = normalize(segment.get("text") or "")
        if current and current == previous_normalized:
            repeat_run += 1
        else:
            repeat_run = 0
        previous_normalized = current or previous_normalized

        if not verdict.suspect and repeat_run >= 2:
            verdict = Verdict(True, "текст повторяется в соседних сегментах")

        segment["suspect"] = verdict.suspect
        if verdict.suspect:
            segment["suspect_reason"] = verdict.reason
        else:
            segment.pop("suspect_reason", None)

    return segments


def usable_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Сегменты, пригодные для отбора моментов и субтитров."""
    return [segment for segment in segments if not segment.get("suspect")]
