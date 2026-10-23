import json
from typing import Any

import pytest

from backend.app.config import Settings
from backend.app.nugen.errors import NugenRateLimitError
from backend.app.nugen.schemas import (
    BaseModelSummary,
    BaseModelsResponse,
    ChatCompletionResponse,
    CompletionChoice,
    CompletionMessage,
)
from backend.app.router import RouterDecision, _load_examples, route


def inference_ready_models(ready: bool = True) -> BaseModelsResponse:
    return BaseModelsResponse(
        models=[
            BaseModelSummary(
                model_id="routing-base",
                model_name="Routing Base",
                type="text-generation",
                inference_ready=ready,
            )
        ]
    )


class FakeNugenClient:
    def __init__(self, content: str | None = None, *, error: Exception | None = None, ready: bool = True):
        self.content = content
        self.error = error
        self.ready = ready
        self.list_calls: list[dict[str, Any]] = []
        self.completion_calls: list[Any] = []

    async def list_base_models(self, **kwargs: Any) -> BaseModelsResponse:
        self.list_calls.append(kwargs)
        return inference_ready_models(self.ready)

    async def chat_completions(self, request: Any) -> ChatCompletionResponse:
        self.completion_calls.append(request)
        if self.error:
            raise self.error
        return ChatCompletionResponse(
            id="test-completion",
            object="chat.completion",
            created=1,
            model=request.model,
            choices=[
                CompletionChoice(
                    index=0,
                    message=CompletionMessage(role="assistant", content=self.content),
                )
            ],
        )


def test_router_examples_include_cross_domain_and_out_of_scope_cases() -> None:
    examples = _load_examples()
    assert len(examples) >= 8
    assert any(len(example.experts) > 1 for example in examples)
    assert any(not example.experts for example in examples)


def test_router_decision_rejects_unknown_duplicate_or_empty_experts_without_reason() -> None:
    with pytest.raises(ValueError):
        RouterDecision.model_validate(
            {"experts": ["tax", "weather"], "reason": "bad", "routing_method": "model"}
        )
    with pytest.raises(ValueError):
        RouterDecision.model_validate(
            {"experts": ["tax", "tax"], "reason": "bad", "routing_method": "model"}
        )
    with pytest.raises(ValueError):
        RouterDecision.model_validate(
            {"experts": [], "reason": "No match", "routing_method": "model"}
        )


@pytest.mark.asyncio
async def test_route_uses_only_verified_inference_ready_base_model_and_strict_json() -> None:
    client = FakeNugenClient(json.dumps({"experts": ["tax"], "reason": "Indian tax question."}))
    settings = Settings(_env_file=None, nugen_api_key="test-only-key", nugen_test_model_id="routing-base")

    result = await route("How do income-tax slabs work?", client=client, settings=settings)

    assert result.experts == ["tax"]
    assert result.reason == "Indian tax question."
    assert result.routing_method == "model"
    assert len(client.list_calls) == 1
    assert client.list_calls[0]["inference_ready"] is True
    assert client.list_calls[0]["model_type"] == "text-generation"
    assert len(client.completion_calls) == 1
    assert client.completion_calls[0].model == "routing-base"
    assert client.completion_calls[0].temperature == 0


@pytest.mark.parametrize(
    ("question", "model_experts", "expected"),
    [
        ("Should I choose ELSS or NPS to save tax?", ["tax"], ["tax", "investment"]),
        ("Compare PPF with Section 80C", ["investment"], ["tax", "investment"]),
    ],
)
@pytest.mark.asyncio
async def test_deterministic_post_routing_adds_explicit_cross_domain_terms(
    question: str, model_experts: list[str], expected: list[str]
) -> None:
    client = FakeNugenClient(json.dumps({"experts": model_experts, "reason": "Model choice."}))
    settings = Settings(_env_file=None, nugen_api_key="test-only-key", nugen_test_model_id="routing-base")

    result = await route(question, client=client, settings=settings)

    assert result.experts == expected
    assert result.routing_method == "model"
    assert "deterministic term matching added" in result.reason.casefold()


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("ELSS or NPS to save tax?", ["tax", "investment"]),
        ("PPF vs 80C", ["tax", "investment"]),
        ("What is the SIP?", ["investment"]),
        ("What does an insurance waiting period mean?", ["insurance"]),
    ],
)
@pytest.mark.asyncio
async def test_keyword_fallback_covers_finance_products_and_concepts(
    question: str, expected: list[str]
) -> None:
    settings = Settings(_env_file=None, nugen_api_key="test-only-key")
    result = await route(question, client=FakeNugenClient(), settings=settings)
    assert result.experts == expected
    assert result.routing_method == "fallback"


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        '{"experts":["weather"],"reason":"unknown domain"}',
        '{"experts":["tax","tax"],"reason":"duplicate"}',
        '{"experts":[],"reason":"no match"}',
        '{"experts":["tax"],"reason":"   "}',
        '{"experts":["tax"],"reason":"ok","extra":true}',
    ],
)
@pytest.mark.asyncio
async def test_malformed_model_routes_use_keyword_fallback(content: str) -> None:
    client = FakeNugenClient(content)
    settings = Settings(_env_file=None, nugen_api_key="test-only-key", nugen_test_model_id="routing-base")

    result = await route("How do income-tax slabs work?", client=client, settings=settings)

    assert result.experts == ["tax"]
    assert result.routing_method == "fallback"
    assert "keyword fallback" in result.reason.casefold()


@pytest.mark.asyncio
async def test_upstream_router_error_uses_fallback_without_logging_content() -> None:
    client = FakeNugenClient(error=NugenRateLimitError("safe rate limit message"))
    settings = Settings(_env_file=None, nugen_api_key="test-only-key", nugen_test_model_id="routing-base")

    result = await route("What does an insurance policy cover?", client=client, settings=settings)

    assert result.experts == ["insurance"]
    assert result.routing_method == "fallback"


@pytest.mark.asyncio
async def test_router_model_missing_or_not_verified_does_not_call_chat_completion() -> None:
    settings = Settings(_env_file=None, nugen_api_key="test-only-key")
    missing = FakeNugenClient()
    missing_result = await route("How do tax slabs work?", client=missing, settings=settings)
    assert missing_result.routing_method == "fallback"
    assert missing.completion_calls == []

    not_ready = FakeNugenClient(ready=False)
    settings.nugen_test_model_id = "routing-base"
    not_ready_result = await route("How do tax slabs work?", client=not_ready, settings=settings)
    assert not_ready_result.routing_method == "fallback"
    assert not_ready.completion_calls == []


@pytest.mark.asyncio
async def test_out_of_scope_model_route_is_empty_and_clear() -> None:
    client = FakeNugenClient(json.dumps({"experts": [], "reason": "Out of scope: this is a weather question."}))
    settings = Settings(_env_file=None, nugen_api_key="test-only-key", nugen_test_model_id="routing-base")

    result = await route("What is tomorrow's weather?", client=client, settings=settings)

    assert result.experts == []
    assert "out of scope" in result.reason.casefold()
