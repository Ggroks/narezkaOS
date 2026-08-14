"""Тесты доступа к провайдеру LLM (BAZA.md §25, §49).

Сеть здесь не трогается: проверяется разбор каталога и порядок пригодности.
Каталог у провайдера меняется постоянно, поэтому код обязан переживать
незнакомые и неполные записи, а не падать на них.
"""

from __future__ import annotations

from narezka.core import llm


def model(model_id: str, **extra) -> dict:
    base = {
        "id": model_id,
        "name": model_id,
        "context_length": 128_000,
        "pricing": {"prompt": "0", "completion": "0"},
        "supported_parameters": ["tools", "structured_outputs"],
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    }
    base.update(extra)
    return base


def test_free_is_detected_by_price_not_only_by_name() -> None:
    """Суффикс — соглашение об именовании, цена — факт."""
    parsed = llm.parse_models({"data": [model("vendor/model-without-suffix")]})
    assert parsed[0].is_free


def test_paid_model_is_not_free() -> None:
    parsed = llm.parse_models(
        {"data": [model("vendor/paid", pricing={"prompt": "0.000002", "completion": "0.000008"})]}
    )
    assert not parsed[0].is_free


def test_free_suffix_wins_when_price_is_missing() -> None:
    parsed = llm.parse_models({"data": [model("vendor/model:free", pricing={})]})
    assert parsed[0].is_free


def test_broken_entries_do_not_break_the_catalogue() -> None:
    """Новое поле или мусорная запись не должны ронять команду."""
    parsed = llm.parse_models(
        {"data": [{"nonsense": True}, model("vendor/ok"), {"id": 42}]}
    )
    assert [m.id for m in parsed] == ["vendor/ok"]


def test_missing_fields_get_safe_defaults() -> None:
    parsed = llm.parse_models({"data": [{"id": "vendor/bare"}]})
    entry = parsed[0]
    assert entry.context_length == 0
    assert entry.structured is False
    assert entry.tools is False
    assert not entry.usable_context


def test_empty_catalogue_is_not_an_error() -> None:
    assert llm.parse_models({}) == []


def test_schema_support_outranks_context_length() -> None:
    """Разбор ответа регулярками — лишний источник ошибок в самой
    ответственной стадии (§53, приоритет 1), поэтому схема важнее длины."""
    parsed = llm.parse_models(
        {
            "data": [
                model("vendor/huge-no-schema", context_length=1_000_000, supported_parameters=[]),
                model("vendor/small-with-schema", context_length=128_000),
            ]
        }
    )
    assert llm.rank_free(parsed)[0].id == "vendor/small-with-schema"


def test_longer_context_wins_among_equals() -> None:
    parsed = llm.parse_models(
        {
            "data": [
                model("vendor/short", context_length=64_000),
                model("vendor/long", context_length=262_000),
            ]
        }
    )
    assert llm.rank_free(parsed)[0].id == "vendor/long"


def test_too_small_context_is_ranked_below() -> None:
    """Окно транскрипта должно помещаться целиком (§26)."""
    parsed = llm.parse_models(
        {
            "data": [
                model("vendor/tiny", context_length=8_000),
                model("vendor/enough", context_length=64_000),
            ]
        }
    )
    ranked = llm.rank_free(parsed)
    assert ranked[0].id == "vendor/enough"
    assert not parsed[0].usable_context


def test_paid_models_are_excluded_from_free_ranking() -> None:
    parsed = llm.parse_models(
        {
            "data": [
                model("vendor/paid", pricing={"prompt": "0.001", "completion": "0.002"}),
                model("vendor/free:free"),
            ]
        }
    )
    assert [m.id for m in llm.rank_free(parsed)] == ["vendor/free:free"]


def test_response_format_counts_as_schema_support() -> None:
    parsed = llm.parse_models(
        {"data": [model("vendor/rf", supported_parameters=["response_format"])]}
    )
    assert parsed[0].structured


def test_music_generator_is_excluded_even_though_it_returns_text() -> None:
    """Реальный случай из каталога: google/lyria-3 — генератор музыки.

    Он принимает текст, объявляет вывод «text+audio» и стоит ноль, поэтому
    по цене и по наличию текста неотличим от языковой модели — и всплывал
    первым в списке рекомендаций с контекстом в миллион токенов. Отличает
    его именно медийный вывод.
    """
    parsed = llm.parse_models(
        {
            "data": [
                model(
                    "google/lyria-3-pro-preview",
                    architecture={
                        "input_modalities": ["text", "image"],
                        "output_modalities": ["text", "audio"],
                    },
                    context_length=1_048_576,
                    supported_parameters=["max_tokens", "response_format"],
                ),
                model("vendor/text"),
            ]
        }
    )
    assert [m.id for m in llm.rank_free(parsed)] == ["vendor/text"]


def test_multimodal_input_is_fine_if_output_is_text() -> None:
    """Модель, принимающая картинки, но отвечающая текстом, нам годится."""
    parsed = llm.parse_models(
        {
            "data": [
                model(
                    "vendor/vision",
                    architecture={
                        "input_modalities": ["text", "image", "video"],
                        "output_modalities": ["text"],
                    },
                )
            ]
        }
    )
    assert parsed[0].is_text


def test_missing_modality_is_assumed_textual() -> None:
    """Отбросить годную модель хуже, чем показать лишнюю: лишнюю видно
    сразу, а отсутствующую — нет."""
    parsed = llm.parse_models({"data": [{"id": "vendor/bare"}]})
    assert parsed[0].is_text


def test_api_key_reads_environment() -> None:
    assert llm.api_key({llm.API_KEY_ENV: "sk-or-test"}) == "sk-or-test"


def test_blank_key_is_treated_as_absent() -> None:
    """Пустая переменная в .env — частая ошибка, и она должна читаться
    как «ключа нет», а не как ключ из пробелов."""
    assert llm.api_key({llm.API_KEY_ENV: "   "}) is None
    assert llm.api_key({}) is None


# --- повторы и переход на запасные ----------------------------------------


class FakeResponse:
    def __init__(self, status: int, payload: dict) -> None:
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """Подменяет httpx: последовательность ответов задаётся заранее."""

    def __init__(self, responses: dict[str, list[FakeResponse]]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def post(self, _path: str, json: dict) -> FakeResponse:
        model = json["model"]
        self.calls.append(model)
        queue = self.responses.get(model)
        if not queue:
            return FakeResponse(404, {"error": {"message": "нет такой модели"}})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def ok(model: str) -> FakeResponse:
    return FakeResponse(200, {"model": model, "choices": [{"message": {"content": "работает"}}]})


def busy(model: str) -> FakeResponse:
    return FakeResponse(
        429,
        {
            "error": {
                "message": "Provider returned error",
                "metadata": {
                    "raw": f"{model} is temporarily rate-limited upstream.",
                    "provider_name": "Google AI Studio",
                },
            }
        },
    )


def run_chat(monkeypatch, responses, models, **kwargs):
    client = FakeClient(responses)
    monkeypatch.setattr(llm, "_client", lambda *_a, **_k: client)
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
    return client, llm.chat("k", models, {"messages": []}, **kwargs)


def test_busy_model_is_retried(monkeypatch) -> None:
    """429 у бесплатной модели — занятость общего пула, а не отказ навсегда."""
    responses = {"a": [busy("a"), busy("a"), ok("a")]}
    client, result = run_chat(monkeypatch, responses, ["a"], max_retries=2)
    assert result["model"] == "a"
    assert client.calls == ["a", "a", "a"]


def test_falls_back_to_the_next_model(monkeypatch) -> None:
    responses = {"a": [busy("a")], "b": [ok("b")]}
    client, result = run_chat(monkeypatch, responses, ["a", "b"], max_retries=1)
    assert result["model"] == "b"
    assert client.calls == ["a", "a", "b"]


def test_permanent_error_skips_retries(monkeypatch) -> None:
    """404 «нет доступа» повтором не лечится — время тратить незачем."""
    responses = {"a": [FakeResponse(404, {"error": {"message": "нет доступа"}})], "b": [ok("b")]}
    client, result = run_chat(monkeypatch, responses, ["a", "b"], max_retries=3)
    assert result["model"] == "b"
    assert client.calls == ["a", "b"]


def test_all_models_failing_reports_every_reason(monkeypatch) -> None:
    """Сообщение должно объяснять, что произошло с каждой моделью:
    иначе «не работает» неотличимо от «занято»."""
    import pytest

    responses = {"a": [busy("a")], "b": [busy("b")]}
    client = FakeClient(responses)
    monkeypatch.setattr(llm, "_client", lambda *_a, **_k: client)
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)

    with pytest.raises(llm.LlmError) as exc:
        llm.chat("k", ["a", "b"], {"messages": []}, max_retries=0)

    assert "rate-limited" in str(exc.value)
    assert "a" in str(exc.value) and "b" in str(exc.value)


def test_empty_model_list_is_rejected() -> None:
    import pytest

    with pytest.raises(llm.LlmError):
        llm.chat("k", [], {"messages": []})


def test_error_text_prefers_the_real_reason() -> None:
    """У OpenRouter в `message` лежит бесполезное «Provider returned error»,
    а настоящая причина — в metadata.raw. Без неё занятость пула выглядит
    как поломка ключа."""
    text = llm._error_text(busy("gemma"))
    assert "rate-limited" in text
    assert "Google AI Studio" in text
    assert "Provider returned error" not in text
