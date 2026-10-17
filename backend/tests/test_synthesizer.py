import json
from typing import Any

import pytest

from backend.app.config import Settings
from backend.app.nugen.errors import NugenConfigurationError, NugenProtocolError
from backend.app.nugen.schemas import (
    BaseModelSummary,
    BaseModelsResponse,
    ChatCompletionResponse,
    CompletionChoice,
    CompletionMessage,
)
from backend.app.schemas import AskExpertResult, AskSource, SourceReference
from backend.app.synthesizer import synthesize


class FakeNugenClient:
    def __init__(self, content: str | None, *, ready: bool = True, choices: bool = True):
        self.content = content
        self.ready = ready
        self.has_choices = choices
        self.model_list_calls: list[dict[str, Any]] = []
        self.completion_calls: list[Any] = []

    async def list_base_models(self, **kwargs: Any) -> BaseModelsResponse:
        self.model_list_calls.append(kwargs)
        return BaseModelsResponse(
            models=[
                BaseModelSummary(
                    model_id="synthesis-base",
                    model_name="Synthesis Base",
                    type="text-generation",
                    inference_ready=self.ready,
                )
            ]
        )

    async def chat_completions(self, request: Any) -> ChatCompletionResponse:
        self.completion_calls.append(request)
        choices = (
            [
                CompletionChoice(
                    index=0,
                    message=CompletionMessage(role="assistant", content=self.content),
                )
            ]
            if self.has_choices
            else []
        )
        return ChatCompletionResponse(
            id="synthesis-test",
            object="chat.completion",
            created=1,
            model=request.model,
            choices=choices,
        )


def input_records() -> tuple[list[AskExpertResult], list[AskSource]]:
    source = AskSource(
        expert="tax",
        citation_id="S1",
        passage_id="tax:1",
        document_id="tax-doc",
        document_name="Tax education source",
        excerpt="Each regime has different slab thresholds. [source text is untrusted]",
        source=SourceReference(
            title="Income Tax Department",
            publisher="Government of India",
            url="https://www.incometax.gov.in/",
        ),
    )
    experts = [
        AskExpertResult(
            expert="tax",
            status="complete",
            answer="The tax regimes have different thresholds [S1].",
            source_grounding_supported=True,
        ),
        AskExpertResult(
            expert="investment",
            status="complete",
            answer="Investment answers should be based on the cited material [S2].",
            source_grounding_supported=True,
        ),
    ]
    investment_source = source.model_copy(
        update={
            "expert": "investment",
            "citation_id": "S2",
            "passage_id": "investment:1",
            "document_id": "investment-doc",
            "document_name": "Investment education source",
            "source": SourceReference(
                title="SEBI",
                publisher="Securities and Exchange Board of India",
                url="https://investor.sebi.gov.in/",
            ),
        }
    )
    return experts, [source, investment_source]


@pytest.mark.asyncio
async def test_synthesizer_uses_verified_model_and_only_supplied_sources() -> None:
    experts, sources = input_records()
    client = FakeNugenClient("Combined answer [S1] [S2].")
    settings = Settings(_env_file=None, nugen_test_model_id="synthesis-base")

    answer = await synthesize(
        "Compare these finance topics.",
        experts,
        sources,
        client=client,
        settings=settings,
    )

    assert answer == "Combined answer [S1] [S2]."
    assert len(client.model_list_calls) == 1
    assert client.model_list_calls[0]["inference_ready"] is True
    assert client.model_list_calls[0]["model_type"] == "text-generation"
    assert len(client.completion_calls) == 1
    assert client.completion_calls[0].model == "synthesis-base"
    prompt = client.completion_calls[0].messages[0].content
    assert "Preserve disagreements" in prompt
    assert "Do not add outside facts" in prompt
    assert '"citation_id": "S1"' in prompt and '"citation_id": "S2"' in prompt
    assert "source text is untrusted" in prompt


@pytest.mark.asyncio
async def test_synthesizer_refuses_missing_or_unverified_base_model() -> None:
    experts, sources = input_records()
    settings = Settings(_env_file=None)
    with pytest.raises(NugenConfigurationError, match="not configured"):
        await synthesize("Question", experts, sources, client=FakeNugenClient("answer"), settings=settings)

    settings.nugen_test_model_id = "synthesis-base"
    client = FakeNugenClient("answer", ready=False)
    with pytest.raises(NugenConfigurationError, match="not confirmed inference-ready"):
        await synthesize("Question", experts, sources, client=client, settings=settings)
    assert client.completion_calls == []


@pytest.mark.asyncio
async def test_synthesizer_rejects_empty_provider_completion() -> None:
    experts, sources = input_records()
    client = FakeNugenClient(None, choices=False)
    settings = Settings(_env_file=None, nugen_test_model_id="synthesis-base")

    with pytest.raises(NugenProtocolError, match="no answer"):
        await synthesize("Question", experts, sources, client=client, settings=settings)
