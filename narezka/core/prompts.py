"""Промпты и схема ответа для отбора моментов.

BAZA.md §13, §14, §15. Держатся отдельно от стадии по двум причинам:
их правят чаще всего остального кода, и их нужно версионировать — иначе
непонятно, на какой формулировке получен результат (§63).

Схема ответа задаётся явно, а не «просим вернуть JSON»: свободный текст
приходится разбирать регулярками, и это ровно тот класс хрупкости, от
которого предостерегает §39.
"""

from __future__ import annotations

from typing import Any

from narezka.core.scoring import TEXT_FACTORS, TEXT_PENALTIES

#: Версия промпта. Пишется в результат, чтобы через месяц было видно,
#: на какой формулировке получен отбор.
#: v2 — у модели перестали спрашивать звук: на первом прогоне она честно
#: ставила ноль всем клипам подряд, потому что читает текст, а не слушает.
PROMPT_VERSION = 2

#: Типы клипов. Взяты из таксономии виральности upstream (docs/upstream-notes.md)
#: и дополнены гейминговыми: спека нацелена на стримы (§13).
CLIP_TYPES = (
    "hook",            # цепляет с первой секунды
    "emotional_peak",  # сильная эмоция, крик, смех
    "revelation",      # неожиданность, раскрытие
    "conflict",        # спор, столкновение
    "victory",         # победа, удачный момент
    "failure",         # провал, поражение
    "quotable",        # фраза, которую хочется повторить
    "story",           # история с началом и концом
    "practical",       # полезное объяснение
    "other",
)

SYSTEM_PROMPT = """\
Ты отбираешь моменты для коротких вертикальных роликов из записи стрима.

Что ищем: смешное, неожиданное, сильные реакции, крики, победы, поражения, \
конфликты, споры, важные игровые моменты, мемное, эмоциональные кульминации, \
истории с началом и концом.

Главное различие, которое ты обязан проводить: **интересно зрителю** — это не \
то же самое, что **просто громко**. Громкий крик без контекста ничего не стоит \
и получает низкие оценки. Момент ценен, когда зрителю понятно, что происходит \
и почему это важно.

Для каждого фрагмента ты делаешь две вещи.

1. Уточняешь границы. Начало — там, где зритель получает контекст, а не за \
десять секунд до события. Конец — после развязки: реакции, результата, \
завершения фразы. Не обрывай на полуслове и не заканчивай на «я сейчас…». \
Опирайся на временные метки реплик и оставайся в пределах предложенного окна \
с небольшим запасом.

2. Оцениваешь по факторам, каждый от 0.0 до 1.0:
   - semantic — понятно ли, что происходит, есть ли смысл;
   - emotion — сила эмоции;
   - context — достаточно ли контекста, чтобы понять момент без остального видео;
   - completeness — законченность: есть завязка и развязка;
   - novelty — необычность, неожиданность.

И штраф, тоже от 0.0 до 1.0:
   - unresolved_ending — момент обрывается без развязки.

Про звук тебя не спрашивают: ты читаешь расшифровку и не слышишь записи. Громкость и наличие музыки измеряются отдельно.

Итоговую оценку не считай — её вычисляет программа по своим весам.

В поле explanation одним предложением по-русски объясни, почему момент \
интересен или почему нет. Без объяснения оценка бесполезна для отладки.

Отвечай на русском языке.\
"""


def response_schema() -> dict[str, Any]:
    """Схема ответа для параметра response_format."""
    factor_props = {
        name: {"type": "number", "minimum": 0, "maximum": 1} for name in TEXT_FACTORS
    }
    penalty_props = {
        name: {"type": "number", "minimum": 0, "maximum": 1} for name in TEXT_PENALTIES
    }

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "clip_selection",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["clips"],
                "properties": {
                    "clips": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "index", "start", "end", "clip_type",
                                "factors", "penalties", "explanation",
                            ],
                            "properties": {
                                "index": {"type": "integer"},
                                "start": {"type": "number"},
                                "end": {"type": "number"},
                                "clip_type": {"type": "string", "enum": list(CLIP_TYPES)},
                                "factors": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": list(TEXT_FACTORS),
                                    "properties": factor_props,
                                },
                                "penalties": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": list(TEXT_PENALTIES),
                                    "properties": penalty_props,
                                },
                                "explanation": {"type": "string"},
                            },
                        },
                    }
                },
            },
        },
    }


def format_time(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def render_candidate(index: int, candidate: dict[str, Any], lines: list[dict[str, Any]]) -> str:
    """Один фрагмент в виде, пригодном для чтения моделью.

    Реплики идут с метками времени: без них модель не может назвать границу
    числом, а §14 требует именно смысловой границы, а не сдвига на константу.
    """
    header = (
        f"### Фрагмент {index}\n"
        f"Предложенное окно: {candidate['start']:.1f}–{candidate['end']:.1f} с "
        f"({format_time(candidate['start'])}–{format_time(candidate['end'])})"
    )
    if not lines:
        return f"{header}\n(речи не распознано)"

    body = "\n".join(
        f"[{line['start']:.1f}] {line['text']}" for line in lines if line.get("text")
    )
    return f"{header}\n{body}"


def build_user_message(blocks: list[str]) -> str:
    return (
        "Оцени фрагменты ниже. Для каждого верни его номер, уточнённые границы "
        "в секундах, тип, факторы, штрафы и объяснение.\n\n" + "\n\n".join(blocks)
    )
