import json
from typing import Any

import pytest

from backend.app.evidence_retrieval import RetrievedPassage
from backend.app.nugen.claim_verifier import NugenClaimVerifier
from backend.app.nugen.errors import NugenProtocolError
from backend.app.nugen.schemas import (
    BaseModelSummary,
    BaseModelsResponse,
    ChatCompletionResponse,
    CompletionChoice,
    CompletionMessage,
)
from backend.app.schemas import SourceReference


class FakeVerifierClient:
    def __init__(self, assessment_json: str, *, inference_ready: bool = True) -> None:
        self.assessment_json = assessment_json
        self.inference_ready = inference_ready
        self.base_model_calls: list[dict[str, Any]] = []
        self.completion_calls: list[Any] = []

    async def list_base_models(self, **kwargs: Any) -> BaseModelsResponse:
        self.base_model_calls.append(kwargs)
        rows = []
        if self.inference_ready:
            rows.append(
                BaseModelSummary(
                    model_id="fixture-verifier-model",
                    model_name="Fixture verifier",
                    type="text-generation",
                    inference_ready=True,
                )
            )
        return BaseModelsResponse(models=rows)

    async def chat_completions(self, request: Any) -> ChatCompletionResponse:
        self.completion_calls.append(request)
        return ChatCompletionResponse(
            id="fixture-verification",
            object="chat.completion",
            created=1_704_123_600,
            model=request.model,
            choices=[
                CompletionChoice(
                    index=0,
                    message=CompletionMessage(
                        role="assistant",
                        content=self.assessment_json,
                    ),
                    finish_reason="stop",
                )
            ],
        )


def passage() -> RetrievedPassage:
    return RetrievedPassage(
        citation_id="S1",
        passage_id="fixture-document:1",
        document_id="fixture-document",
        document_name="Official investor source",
        excerpt="A systematic investment plan does not guarantee returns.",
        source=SourceReference(
            title="Official investor source",
            url="https://investor.sebi.gov.in/",
        ),
        score=1.0,
    )


@pytest.mark.asyncio
async def test_nugen_verifier_checks_readiness_and_uses_exact_evidence_quotes() -> None:
    response = json.dumps(
        {
            "status": "supported",
            "evidence": [
                {
                    "evidence_id": "S1",
                    "quote": "A systematic investment plan does not guarantee returns.",
                }
            ],
        }
    )
    client = FakeVerifierClient(response)
    verifier = NugenClaimVerifier(client, "fixture-verifier-model")

    assessment = await verifier.assess(
        "A systematic investment plan does not guarantee returns.",
        [passage()],
    )

    assert assessment.status == "supported"
    assert assessment.evidence[0].evidence_id == "S1"
    assert client.base_model_calls == [
        {
            "inference_ready": True,
            "model_type": "text-generation",
            "limit": 100,
            "offset": 0,
        }
    ]
    request = client.completion_calls[0]
    assert request.model == "fixture-verifier-model"
    assert request.temperature == 0
    assert json.loads(request.messages[1].content)["evidence"][0]["text"] == passage().excerpt


@pytest.mark.asyncio
async def test_nugen_verifier_refuses_unverified_model_id_without_calling_completion() -> None:
    client = FakeVerifierClient("{}", inference_ready=False)
    verifier = NugenClaimVerifier(client, "fixture-verifier-model")

    with pytest.raises(NugenProtocolError, match="not returned as an inference-ready"):
        await verifier.assess("A claim.", [passage()])

    assert client.completion_calls == []


@pytest.mark.asyncio
async def test_nugen_verifier_rejects_malformed_assessment_output() -> None:
    client = FakeVerifierClient("This is not JSON")
    verifier = NugenClaimVerifier(client, "fixture-verifier-model")

    with pytest.raises(NugenProtocolError, match="malformed assessment JSON"):
        await verifier.assess("A claim.", [passage()])
