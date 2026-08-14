"""Подготовка текстов для публикации: заголовок, описание, хэштеги.

BAZA.md §22, §23, §24. Модель предлагает варианты, а выбирает **система** —
по проверяемым признакам, а не по ощущению. Иначе «выбор наиболее
привлекательного» превращается в случайность, которую нельзя ни повторить,
ни отладить.

§22 отдельно запрещает ложный кликбейт: заголовок обязан соответствовать
содержанию. Проверить соответствие автоматически нельзя, а вот отсечь
типовые приёмы завлечения — можно, и это делается здесь.
"""

from __future__ import annotations

import re
from typing import Any

#: Практический предел для вертикальных роликов. Длиннее — обрезается
#: интерфейсом платформы, и конец заголовка не увидит никто.
MAX_TITLE_CHARS = 60

MAX_HASHTAGS = 8
MAX_HASHTAG_CHARS = 24

#: Приёмы завлечения, которые §22 запрещает. Проверяется соответствие
#: заголовка содержанию мы не можем, но обещание, которое нельзя выполнить
#: в тридцатисекундном ролике, видно по формулировке.
CLICKBAIT_PATTERNS = (
    r"\bты не поверишь\b",
    r"\bшок\b",
    r"\bсенсаци",
    r"\bвсе в шоке\b",
    r"\bчто было дальше\b",
    r"\bэто изменит\b",
    r"\bникто не ожидал\b",
    r"\byou won'?t believe\b",
    r"\bshocking\b",
    r"\bgone wrong\b",
)

_CLICKBAIT = re.compile("|".join(CLICKBAIT_PATTERNS), re.IGNORECASE | re.UNICODE)

#: Хэштеги-пустышки: накрутка охвата, не описывающая содержание (§24).
BANNED_HASHTAGS = frozenset(
    {
        "fyp", "foryou", "foryoupage", "рекомендации", "врек", "вреки",
        "хочуврек", "подпишись", "лайк", "like4like", "follow4follow",
        "viral", "вирус", "тренды", "trending",
    }
)

_HASHTAG_CHARS = re.compile(r"[^0-9A-Za-zА-Яа-яЁё_]", re.UNICODE)
_MANY_CAPS = re.compile(r"[A-ZА-ЯЁ]{5,}")


def is_clickbait(title: str) -> bool:
    """Типовые приёмы завлечения и крик капслоком."""
    return bool(_CLICKBAIT.search(title)) or bool(_MANY_CAPS.search(title))


def title_problems(title: str) -> list[str]:
    """Почему заголовок не годится. Пустой список — годится."""
    text = (title or "").strip()
    problems = []
    if not text:
        problems.append("пустой")
    if len(text) > MAX_TITLE_CHARS:
        problems.append(f"длиннее {MAX_TITLE_CHARS} символов")
    if is_clickbait(text):
        problems.append("похоже на кликбейт")
    return problems


def choose_title(variants: list[str], fallback: str = "") -> tuple[str, list[dict[str, Any]]]:
    """Первый вариант без нареканий и разбор всех остальных.

    Порядок вариантов задаёт модель — он и есть её предпочтение. Система
    лишь отбраковывает то, что нарушает правила, поэтому решение
    воспроизводимо и объяснимо: видно, какой вариант отвергнут и почему.
    """
    reviewed = []
    chosen = ""
    for variant in variants:
        text = (variant or "").strip()
        problems = title_problems(text)
        reviewed.append({"title": text, "problems": problems})
        if not problems and not chosen:
            chosen = text

    if not chosen:
        # Все варианты забракованы — берём наименее плохой: слишком длинный
        # можно обрезать, а вот кликбейт лучше не публиковать вовсе.
        for entry in reviewed:
            if entry["problems"] == [f"длиннее {MAX_TITLE_CHARS} символов"]:
                chosen = entry["title"][: MAX_TITLE_CHARS - 1].rstrip() + "…"
                break

    return chosen or fallback.strip(), reviewed


def clean_hashtags(raw: list[str], limit: int = MAX_HASHTAGS) -> list[str]:
    """Приводит хэштеги к виду, пригодному для публикации (§24).

    Убирает решётки, мусорные символы, повторы и накрутку охвата. Порядок
    сохраняется: первыми модель ставит более осмысленные.
    """
    result: list[str] = []
    seen: set[str] = set()

    for item in raw:
        tag = _HASHTAG_CHARS.sub("", str(item or "").strip().lstrip("#"))
        if not tag or len(tag) < 2 or len(tag) > MAX_HASHTAG_CHARS:
            continue
        if tag.isdigit():
            continue
        lowered = tag.lower()
        if lowered in BANNED_HASHTAGS or lowered in seen:
            continue
        seen.add(lowered)
        result.append(f"#{tag}")
        if len(result) >= limit:
            break

    return result


def build_entry(
    clip: dict[str, Any],
    answer: dict[str, Any],
) -> dict[str, Any]:
    """Собирает готовую к публикации запись по одному клипу."""
    variants = [v for v in (answer.get("titles") or []) if isinstance(v, str)]
    title, reviewed = choose_title(variants, fallback=clip.get("explanation", ""))
    hashtags = clean_hashtags(answer.get("hashtags") or [])

    return {
        "index": clip["index"],
        "clip_id": clip.get("clip_id"),
        "start": clip["start"],
        "end": clip["end"],
        "title": title,
        "title_variants": reviewed,
        "description": (answer.get("description") or "").strip(),
        "hashtags": hashtags,
    }


def render_description(entry: dict[str, Any]) -> str:
    """Описание вместе с хэштегами — то, что копируют в поле публикации."""
    parts = [entry["description"].strip()]
    if entry["hashtags"]:
        parts.append(" ".join(entry["hashtags"]))
    return "\n\n".join(part for part in parts if part)
