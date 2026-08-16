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
import time
from dataclasses import dataclass
from typing import Any

import httpx

@dataclass(frozen=True)
class Provider:
    """Куда ходить за моделью. Протокол один и тот же — OpenAI-совместимый,
    поэтому переход сводится к смене адреса и переменной с ключом."""

    name: str
    base_url: str
    key_env: str
    #: Заголовки сверх авторизации. OpenRouter просит их для учёта трафика.
    headers: dict[str, str]
    #: Есть ли у провайдера открытый каталог моделей.
    lists_models: bool


PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        headers={
            "HTTP-Referer": "https://github.com/Ggroks/narezkaOS",
            "X-Title": "Narezka OS",
        },
        lists_models=True,
    ),
    "openai": Provider(
        name="openai",
        base_url="https://api.openai.com/v1",
        key_env="OPENAI_API_KEY",
        headers={},
        lists_models=True,
    ),
}

DEFAULT_PROVIDER = "openrouter"


def provider(name: str | None = None) -> Provider:
    """Настройки провайдера по имени. Незнакомое имя — понятная ошибка,
    а не запрос в никуда."""
    key = (name or DEFAULT_PROVIDER).strip().lower()
    if key not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise LlmError(f"неизвестный провайдер «{name}». Доступны: {known}")
    return PROVIDERS[key]


#: Оставлено для совместимости: часть кода и сообщений ссылается на них прямо.
BASE_URL = PROVIDERS[DEFAULT_PROVIDER].base_url
API_KEY_ENV = PROVIDERS[DEFAULT_PROVIDER].key_env

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


class RateLimited(LlmError):
    """Модель занята. Отдельный класс: это временно и лечится ожиданием
    или переходом на другую модель, а не правкой запроса."""


#: Коды, при которых имеет смысл повторить попытку. 429 у бесплатных моделей
#: означает не исчерпанную квоту ключа, а занятость общего пула провайдера.
RETRIABLE = frozenset({408, 429, 500, 502, 503, 504})

#: Пауза перед повтором удваивается, начиная с этого значения. Держится
#: небольшой намеренно: когда занят общий пул провайдера, перейти к другой
#: модели быстрее, чем досидеть очередь к этой.
RETRY_BASE_DELAY = 2.0


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


def api_key(env: dict[str, str] | None = None, provider_name: str | None = None) -> str | None:
    source = env if env is not None else os.environ
    key = (source.get(provider(provider_name).key_env) or "").strip()
    return key or None


def _client(key: str | None, timeout: float, provider_name: str | None = None) -> httpx.Client:
    current = provider(provider_name)
    headers = dict(current.headers)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return httpx.Client(base_url=current.base_url, headers=headers, timeout=timeout)


def fetch_models(
    key: str | None = None, timeout: float = 30.0, provider_name: str | None = None
) -> list[ModelInfo]:
    """Актуальный каталог. У OpenRouter он открытый, у OpenAI нужен ключ."""
    try:
        with _client(key, timeout, provider_name) as client:
            response = client.get("/models")
            response.raise_for_status()
            return parse_models(response.json())
    except httpx.HTTPStatusError as exc:
        raise LlmError(f"каталог моделей вернул {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise LlmError(f"не удалось получить каталог моделей: {exc}") from exc
    except ValueError as exc:
        raise LlmError(f"каталог моделей вернул не JSON: {exc}") from exc


def _post_once(client: httpx.Client, model: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        response = client.post("/chat/completions", json={**payload, "model": model})
    except httpx.HTTPError as exc:
        raise LlmError(f"запрос не прошёл: {exc}") from exc

    if response.status_code == 200:
        return response.json()

    detail = f"{model}: {_error_text(response)}"
    if response.status_code in RETRIABLE:
        raise RateLimited(detail)
    raise LlmError(detail)


def process_batches(
    batches: list[Any],
    ask: Any,
    log: Any,
    *,
    label: str = "пакет",
    retry_rounds: int = 2,
    pause: float = 20.0,
) -> tuple[dict[int, Any], set[str], list[str]]:
    """Прогоняет пакеты через модель, повторяя упавшие отдельным заходом.

    Замерено на пятичасовой записи: один пакет из тридцати отказал, и шесть
    роликов остались без текстов при живой в остальном стадии. Пропуск внутри
    прохода — не отказоустойчивость: у бесплатных моделей занятость пула
    обычное состояние, и та же попытка минутой позже проходит.

    Поэтому упавшие складываются и повторяются **после** основного прохода:
    к тому моменту пул успевает освободиться, а пауза между заходами не
    тормозит удачные пакеты. Возвращает ответы, использованные модели и
    описания отказов, переживших все заходы.
    """
    answers: dict[int, Any] = {}
    models_used: set[str] = set()
    pending = list(enumerate(batches, start=1))
    failures: dict[int, str] = {}

    for round_number in range(retry_rounds + 1):
        if not pending:
            break
        if round_number:
            log.info("повтор %d: пакетов %d", round_number, len(pending))
            time.sleep(pause)

        retry: list[tuple[int, Any]] = []
        for number, batch in pending:
            log.info("%s %d из %d", label, number, len(batches))
            try:
                answered, model = ask(batch)
            except (LlmError, ValueError) as exc:
                failures[number] = f"{label} {number}: {exc}"
                # Ошибка разбора ответа не лечится ожиданием: модель ответила,
                # но не тем. Повторять стоит только временный отказ.
                if isinstance(exc, RateLimited) or not isinstance(exc, ValueError):
                    retry.append((number, batch))
                log.warning("%s %d не прошёл — %s", label, number, exc)
                continue
            answers.update(answered)
            models_used.add(model)
            failures.pop(number, None)
        pending = retry

    return answers, models_used, list(failures.values())


def chat(
    key: str,
    models: list[str],
    payload: dict[str, Any],
    *,
    timeout: float = 120.0,
    max_retries: int = 3,
    on_attempt: Any = None,
    provider_name: str | None = None,
) -> dict[str, Any]:
    """Запрос к модели с повторами и переходом на запасные.

    У бесплатных моделей отказ по занятости — обычное состояние, а не сбой:
    пул делится между всеми пользователями провайдера. Поэтому сначала
    ждём и повторяем, а исчерпав попытки, берём следующую модель из списка.

    Возвращает ответ провайдера. Какая модель на самом деле ответила, видно
    в поле `model` — записывать нужно именно её (§63), а не ту, что просили.
    """
    if not models:
        raise LlmError("не задано ни одной модели")

    failures: list[str] = []
    with _client(key, timeout, provider_name) as client:
        for model in models:
            for attempt in range(max_retries + 1):
                try:
                    if on_attempt:
                        on_attempt(model, attempt)
                    return _post_once(client, model, payload)
                except RateLimited as exc:
                    failures.append(str(exc))
                    if attempt < max_retries:
                        time.sleep(RETRY_BASE_DELAY * (2**attempt))
                except LlmError as exc:
                    # Не временная ошибка — повторять бессмысленно,
                    # сразу к следующей модели.
                    failures.append(str(exc))
                    break

    raise LlmError("ни одна модель не ответила:\n  " + "\n  ".join(dict.fromkeys(failures)))


def check_key(
    key: str,
    models: list[str],
    timeout: float = 60.0,
    max_retries: int = 2,
    on_attempt: Any = None,
    provider_name: str | None = None,
) -> dict[str, Any]:
    """Пробный запрос: работает ли ключ и отвечает ли хоть одна модель.

    Проверять до запуска стадии дешевле, чем узнать об отказе на середине
    восьмичасового прогона.
    """
    data = chat(
        key,
        models,
        {
            "messages": [{"role": "user", "content": "Ответь одним словом: работает"}],
            "max_tokens": 16,
        },
        timeout=timeout,
        max_retries=max_retries,
        on_attempt=on_attempt,
        provider_name=provider_name,
    )
    choices = data.get("choices") or []
    text = choices[0].get("message", {}).get("content", "") if choices else ""
    return {
        "model": data.get("model") or models[0],
        "reply": (text or "").strip(),
        "usage": data.get("usage") or {},
    }


def _error_text(response: httpx.Response) -> str:
    """Человекочитаемая причина отказа.

    Поле `message` у OpenRouter обычно бесполезно («Provider returned
    error»), а настоящее объяснение лежит в `metadata.raw` — например,
    что модель занята в общем пуле провайдера. Без него ошибка выглядит
    как поломка ключа, хотя ключ в порядке.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]

    error = body.get("error")
    if not isinstance(error, dict):
        return str(error or body)[:300]

    metadata = error.get("metadata") or {}
    parts = [str(metadata.get("raw") or error.get("message") or "отказ без объяснения")]
    provider = metadata.get("provider_name")
    if provider:
        parts.append(f"провайдер: {provider}")
    return " · ".join(parts)[:400]
