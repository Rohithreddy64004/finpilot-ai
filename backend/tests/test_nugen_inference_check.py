import pytest

from backend.app.config import Settings
from backend.app.nugen import inference_check
from backend.app.nugen.errors import NugenConfigurationError
from backend.app.nugen.schemas import (
    BaseModelSummary,
    BaseModelsResponse,
    ChatCompletionResponse,
    CompletionChoice,
    CompletionMessage,
)


@pytest.mark.asyncio
async def test_inference_check_verifies_model_from_list_before_posting(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []

    class MockClient:
        def __init__(self, settings: Settings) -> None:
            assert settings.nugen_test_model_id == "verified-fixture-model"

        async def __aenter__(self) -> "MockClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def list_base_models(self, **kwargs: object) -> BaseModelsResponse:
            captured.append(kwargs)
            assert kwargs == {"limit": 100, "offset": 0}
            # Match the published OpenAPI response envelope: the rows are under
            # the top-level `models` property, not under `data`.
            return BaseModelsResponse.model_validate(
                {
                    "models": [
                        {
                            "model_id": "verified-fixture-model",
                            "model_name": "Verified fixture model",
                            "type": "text-generation",
                            "alignment_ready": True,
                            "inference_ready": True,
                            "available_on_request": False,
                        }
                    ]
                }
            )

        async def chat_completions(self, request: object) -> ChatCompletionResponse:
            captured.append(request)
            return ChatCompletionResponse(
                id="fixture-response",
                object="chat.completion",
                created=1,
                model="verified-fixture-model",
                choices=[
                    CompletionChoice(
                        index=0,
                        message=CompletionMessage(
                            role="assistant",
                            content="Compound interest earns interest on prior interest.",
                        ),
                        finish_reason="stop",
                    )
                ],
            )

    monkeypatch.setattr(inference_check, "NugenClient", MockClient)
    settings = Settings(
        _env_file=None,
        nugen_api_key="mock-secret",
        nugen_test_model_id="verified-fixture-model",
    )

    model_id, answer = await inference_check.run_inference_check(settings)

    assert model_id == "verified-fixture-model"
    assert answer == "Compound interest earns interest on prior interest."
    assert len(captured) == 2
    request = captured[1]
    assert request.model == "verified-fixture-model"
    assert request.messages[0].role == "user"
    assert request.stream is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_row", "expected_reason", "expected_readiness"),
    [
        (
            {
                "model_id": "fixture-model",
                "model_name": "Legacy task example",
                "type": "llm",
                "alignment_ready": True,
                "available_on_request": False,
            },
            "returned type is not 'text-generation'",
            "missing (defaults to false)",
        ),
        (
            {
                "model_id": "fixture-model",
                "model_name": "Generation model without readiness value",
                "type": "text-generation",
                "alignment_ready": True,
                "available_on_request": False,
            },
            "omitted inference_ready",
            "missing (defaults to false)",
        ),
        (
            {
                "model_id": "fixture-model",
                "model_name": "Not inference ready",
                "type": "text-generation",
                "inference_ready": False,
                "alignment_ready": True,
                "available_on_request": False,
            },
            "did not explicitly mark inference_ready as true",
            "false",
        ),
    ],
)
async def test_inference_check_keeps_strict_task_and_readiness_validation(
    monkeypatch: pytest.MonkeyPatch,
    model_row: dict[str, object],
    expected_reason: str,
    expected_readiness: str,
) -> None:
    completion_calls = 0
    response = BaseModelsResponse.model_validate({"models": [model_row]})

    class MockClient:
        def __init__(self, settings: Settings) -> None:
            assert settings.nugen_test_model_id == "fixture-model"

        async def __aenter__(self) -> "MockClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def list_base_models(self, **kwargs: object) -> BaseModelsResponse:
            assert kwargs == {"limit": 100, "offset": 0}
            return response

        async def chat_completions(self, request: object) -> ChatCompletionResponse:
            nonlocal completion_calls
            completion_calls += 1
            raise AssertionError("invalid model must not be used for inference")

    monkeypatch.setattr(inference_check, "NugenClient", MockClient)
    settings = Settings(
        _env_file=None,
        nugen_api_key="mock-secret",
        nugen_test_model_id="fixture-model",
    )

    with pytest.raises(NugenConfigurationError) as raised:
        await inference_check.run_inference_check(settings)

    message = str(raised.value)
    assert expected_reason in message
    assert "'fixture-model' |" in message
    assert expected_readiness in message
    assert completion_calls == 0


@pytest.mark.asyncio
async def test_diagnostics_redact_api_key_if_it_appears_in_a_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MockClient:
        def __init__(self, settings: Settings) -> None:
            pass

        async def __aenter__(self) -> "MockClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def list_base_models(self, **kwargs: object) -> BaseModelsResponse:
            return BaseModelsResponse(
                models=[
                    BaseModelSummary(
                        model_id="mock-secret",
                        model_name="Fixture",
                        type="llm",
                        inference_ready=True,
                    )
                ]
            )

    monkeypatch.setattr(inference_check, "NugenClient", MockClient)
    settings = Settings(
        _env_file=None,
        nugen_api_key="mock-secret",
        nugen_test_model_id="mock-secret",
    )

    with pytest.raises(NugenConfigurationError) as raised:
        await inference_check.run_inference_check(settings)

    assert "[REDACTED]" in str(raised.value)
    assert "mock-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_inference_check_fails_clearly_when_model_id_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_client(*_: object, **__: object) -> None:
        raise AssertionError("client should not be created without a model ID")

    monkeypatch.setattr(inference_check, "NugenClient", unexpected_client)
    settings = Settings(_env_file=None, nugen_api_key="mock-secret")

    with pytest.raises(NugenConfigurationError, match="NUGEN_TEST_MODEL_ID"):
        await inference_check.run_inference_check(settings)
