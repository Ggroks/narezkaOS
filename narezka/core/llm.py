"""Доступ к языковой модели через OpenRouter.

BAZA.md §25 и §49: провайдер за интерфейсом, а не прибитый гвоздями вызов.
OpenRouter выбран потому, что говорит на протоколе OpenAI и даёт доступ
к бесплатным моделям — сменить провайдера значит поменять базовый адрес
и имя модели, а не переписывать стадию.

Главное, что нужно знать про бесплатные модели: **их список меняется**.
Модели появляются, исчезают и переименовываются, поэтому имя модели живёт
в конфиге, а не в коде, и есть команда, которая показывает актуальный список
(`narezka models`). Захардкоженное имя протухнет через неделю.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://openrouter.ai/api/v1"

#: Переменная окружения с ключом. Ключ не хранится в конфиге: конфиг
#: в репозитории, а `.env` — в .gitignore.
API_KEY_ENV = "OPENROUTER_API_KEY"

#: Бесплатные модели помечаются этим суффиксом в идентификаторе.
FREE_SUFFIX = ":free"

#: Столько токенов нужно, чтобы окно транскрипта с запасом поместилось
#: целиком (§26: чанки по 1200 с с перекрытием).
MIN_USEFUL_CONTEXT = 32_000

#: Если модель выдаёт что-то из этого, она генератор медиа, а не собеседник.
#: Проверять надо именно вывод: генератор музыки Lyria принимает текст
#: и в выводе объявляет «text+audio», то есть по одному наличию текста
#: он неотличим от языковой модели.
MEDIA_OUTPUTS = frozenset({"audio", "image", "video"})


class LlmError(RuntimeError):
    """Провайдер недоступен или ответил отказом."""


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    context_length: int
    is_free: bool
    #: Принимает и выдаёт текст. Нулевая цена бывает и у генераторов музыки
    #: или картинок — для разбора транскрипта они бесполезны, а в списке
    #: бесплатных стоят наравне с остальными.
    is_text: bool
    #: Поддерживает ли ответ по схеме JSON. Без этого отбор моментов придётся
    #: разбирать регулярками, что ненадёжно (§39).
    structured: bool
    tools: bool

    @property
    def usable_context(self) -> bool:
        return self.context_length >= MIN_USEFUL_CONTEXT

    def score(self) -> tuple[int, int, int, int]:
        """Пригодность под нашу задачу — чем больше, тем лучше.

        Порядок важности: работает ли с текстом вообще, потом ответ по схеме,
        потом длина контекста. Модель без схемы годится, но требует разбора
        текста, а это лишний источник ошибок в самой ответственной
        стадии (§53, приоритет 1).
        """
        return (int(self.is_text), int(self.structured), int(self.usable_context), self.context_length)


def parse_models(payload: dict[str, Any]) -> list[ModelInfo]:
    """Разбирает ответ каталога.

    Устойчив к отсутствующим полям: каталог у провайдера меняется, и падать
    из-за нового необязательного поля стадия не должна.
    """
    result: list[ModelInfo] = []
    for item in payload.get("data", []):
        model_id = item.get("id")
        if not isinstance(model_id, str):
            continue

        pricing = item.get("pricing") or {}
        # Бесплатность определяется ценой, а не только суффиксом: суффикс —
        # соглашение об именовании, а цена — факт.
        prompt_price = _as_float(pricing.get("prompt"))
        completion_price = _as_float(pricing.get("completion"))
        is_free = model_id.endswith(FREE_SUFFIX) or (
            prompt_price == 0.0 and completion_price == 0.0
        )

        supported = item.get("supported_parameters") or []
        architecture = item.get("architecture") or {}
        inputs = architecture.get("input_modalities") or []
        outputs = architecture.get("output_modalities") or []
        # Когда полей нет, считаем модель текстовой: отбросить годную хуже,
        # чем показать лишнюю — лишнюю видно сразу, отсутствующую нет.
        # На входе картинки не мешают, а вот вывод должен быть только текстом.
        accepts_text = "text" in inputs or not inputs
        returns_text = "text" in outputs or not outputs
        returns_media = bool(MEDIA_OUTPUTS.intersection(outputs))
        is_text = accepts_text and returns_text and not returns_media

        result.append(
            ModelInfo(
                id=model_id,
                name=item.get("name") or model_id,
                context_length=int(item.get("context_length") or 0),
                is_free=is_free,
                is_text=is_text,
                structured="structured_outputs" in supported or "response_format" in supported,
                tools="tools" in supported,
            )
        )
    return result


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rank_free(models: list[ModelInfo]) -> list[ModelInfo]:
    """Бесплатные текстовые модели — лучшие первыми."""
    free = [m for m in models if m.is_free and m.is_text]
    return sorted(free, key=lambda m: m.score(), reverse=True)


def api_key(env: dict[str, str] | None = None) -> str | None:
    source = env if env is not None else os.environ
    key = (source.get(API_KEY_ENV) or "").strip()
    return key or None


def _client(key: str | None, timeout: float) -> httpx.Client:
    headers = {
        # OpenRouter просит эти заголовки для учёта трафика приложения.
        "HTTP-Referer": "https://github.com/Ggroks/narezkaOS",
        "X-Title": "Narezka OS",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return httpx.Client(base_url=BASE_URL, headers=headers, timeout=timeout)


def fetch_models(key: str | None = None, timeout: float = 30.0) -> list[ModelInfo]:
    """Актуальный каталог. Ключ необязателен — список открытый."""
    try:
        with _client(key, timeout) as client:
            response = client.get("/models")
            response.raise_for_status()
            return parse_models(response.json())
    except httpx.HTTPStatusError as exc:
        raise LlmError(f"каталог моделей вернул {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise LlmError(f"не удалось получить каталог моделей: {exc}") from exc
    except ValueError as exc:
        raise LlmError(f"каталог моделей вернул не JSON: {exc}") from exc


def check_key(key: str, model: str, timeout: float = 60.0) -> dict[str, Any]:
    """Пробный запрос: работает ли ключ и отвечает ли выбранная модель.

    Проверять до запуска стадии дешевле, чем узнать об отказе на середине
    восьмичасового прогона.
    """
    try:
        with _client(key, timeout) as client:
            response = client.post(
                "/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Ответь одним словом: работает"}],
                    "max_tokens": 16,
                },
            )
    except httpx.HTTPError as exc:
        raise LlmError(f"запрос не прошёл: {exc}") from exc

    if response.status_code != 200:
        raise LlmError(f"провайдер вернул {response.status_code}: {_error_text(response)}")

    data = response.json()
    choices = data.get("choices") or []
    text = choices[0].get("message", {}).get("content", "") if choices else ""
    return {
        "model": data.get("model", model),
        "reply": (text or "").strip(),
        "usage": data.get("usage") or {},
    }


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or body)[:200]
