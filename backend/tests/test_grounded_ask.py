import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.app import ask_api
from backend.app.alignment_jobs import get_alignment_job_repository
from backend.app.config import Settings
from backend.app.interactions import get_interaction_repository
from backend.app.main import app
from backend.app.nugen.schemas import (
    BaseModelSummary,
    BaseModelsResponse,
    ChatCompletionResponse,
    CompletionChoice,
    CompletionMessage,
    DocumentStatusResponse,
)
from backend.app.rate_limit import InMemoryRateLimiter
from backend.app.schemas import ActiveSourceSelection, SourceDocumentRecord
from backend.app.source_content_store import get_source_content_store
from backend.app.workflow_api import get_document_repository


ROOT = Path(__file__).resolve().parents[2]
DOMAINS = ("tax", "investment", "insurance")
CORPUS_NAMES = {
    "tax": "tax_expert.txt",
    "investment": "investment_expert.txt",
    "insurance": "insurance_expert.txt",
}
CORPUS_URLS = {
    "tax": "https://www.incometax.gov.in/iec/foportal/help/individual/return-applicable-1",
    "investment": "https://investor.sebi.gov.in/",
    "insurance": "https://irdai.gov.in/health-dept",
}
QUESTIONS = (
    ("How do income tax slabs work for individuals?", ["tax"]),
    ("What is a SIP?", ["investment"]),
    ("What does a health insurance waiting period mean?", ["insurance"]),
    ("Should I choose ELSS or NPS to save tax?", ["tax", "investment"]),
    ("What's the weather in Hyderabad?", []),
)


class MemoryRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], SourceDocumentRecord] = {}
        self.selections: dict[str, ActiveSourceSelection] = {}
        for domain in DOMAINS:
            path = ROOT / "knowledge_bases" / CORPUS_NAMES[domain]
            content = path.read_bytes()
            document_id = f"active-{domain}-fixture"
            self.records[(domain, document_id)] = SourceDocumentRecord(
                document_id=document_id,
                domain=domain,
                document_name=f"{domain.title()} committed education source",
                original_filename=CORPUS_NAMES[domain],
                source_url=CORPUS_URLS[domain],
                retrieved_on=date(2026, 10, 17),
                uploaded_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
                size_bytes=len(content),
            )
            self.selections[domain] = ActiveSourceSelection(
                domain=domain,
                active_document_ids=[document_id],
                revision=1,
                changed_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
            )

    async def get_active_source_selection(self, domain):
        return self.selections.get(domain)

    async def get(self, domain, document_id):
        return self.records.get((domain, document_id))

    async def update_status(self, *_args, **_kwargs):
        return None


class MemoryContentStore:
    async def get(self, domain, document_id):
        filename = CORPUS_NAMES[domain]
        return (ROOT / "knowledge_bases" / filename).read_bytes()


class MemoryAlignmentRepository:
    async def find_domain_model(self, *_args, **_kwargs):
        return None


class MemoryInteractionRepository:
    def __init__(self) -> None:
        self.queries: list[Any] = []

    async def save_query(self, record) -> None:
        self.queries.append(record)


class MockNugenClient:
    """Provider-shaped fake; no network-capable Nugen client is created in these tests."""

    def __init__(self) -> None:
        self.completion_requests = []
        self.document_status_calls: list[str] = []

    async def get_document_status(self, document_id: str) -> DocumentStatusResponse:
        self.document_status_calls.append(document_id)
        return DocumentStatusResponse(document_id=document_id, status="READY")

    async def list_base_models(self, **_kwargs) -> BaseModelsResponse:
        return BaseModelsResponse(
            models=[
                BaseModelSummary(
                    model_id="fixture-base-model",
                    model_name="Mocked base model",
                    type="text-generation",
                    inference_ready=True,
                )
            ]
        )

    async def chat_completions(self, request) -> ChatCompletionResponse:
        self.completion_requests.append(request)
        system_message = request.messages[0].content
        question = request.messages[-1].content
        if "Route a user's question" in system_message:
            routed, reason = self._route(question)
            answer = json.dumps({"experts": routed, "reason": reason})
        elif "You synthesize educational answers" in system_message:
            answer = self._synthesis_answer(system_message)
        else:
            answer = self._expert_answer(question, system_message)
        return ChatCompletionResponse(
            id="mock-completion",
            object="chat.completion",
            created=1_800_000_000,
            model=request.model,
            choices=[
                CompletionChoice(
                    index=0,
                    message=CompletionMessage(role="assistant", content=answer),
                    finish_reason="stop",
                )
            ],
        )

    @staticmethod
    def _route(question: str) -> tuple[list[str], str]:
        if "income tax slabs" in question.casefold():
            return ["tax"], "This is an individual income-tax question."
        if "sip" in question.casefold():
            return ["investment"], "This is an investment concept question."
        if "waiting period" in question.casefold():
            return ["insurance"], "This is an insurance policy-term question."
        if "elss" in question.casefold() or "nps" in question.casefold():
            return ["tax", "investment"], "This question spans tax and investment topics."
        return [], "Out of scope: this is a weather question."

    @staticmethod
    def _expert_answer(question: str, _system_message: str) -> str:
        lowered = question.casefold()
        if "income tax slabs" in lowered:
            return "The source describes slab rates for bands of taxable income, with separate regime tables. [S1]"
        if "sip" in lowered:
            return "A Systematic Investment Plan is a way to invest a fixed amount regularly in a mutual-fund scheme. [S1]"
        if "waiting period" in lowered:
            return "A waiting period is a period stated in a policy when specified conditions or services are not covered. [S1]"
        if "elss" in lowered or "nps" in lowered:
            return "The retrieved source describes its covered tax or investment terms but does not establish which option fits an individual. [S1]"
        return "The source passage addresses this finance topic. [S1]"

    @staticmethod
    def _synthesis_answer(system_message: str) -> str:
        payload = json.loads(system_message.split("Expert results and passages:\n", 1)[1])
        first_by_expert: dict[str, str] = {}
        for passage in payload["passages"]:
            first_by_expert.setdefault(passage["expert"], passage["citation_id"])
        tax_id = first_by_expert["tax"]
        investment_id = first_by_expert["investment"]
        return (
            "The tax source explains slab rules and the investment source describes ELSS terms; "
            f"the cited material does not establish an ELSS-versus-NPS recommendation [{tax_id}] [{investment_id}]."
        )


@pytest.fixture(autouse=True)
def isolate_api_dependencies(monkeypatch: pytest.MonkeyPatch):
    app.dependency_overrides.clear()
    monkeypatch.setattr(
        app.state,
        "api_rate_limiter",
        InMemoryRateLimiter(max_requests=10_000, window_seconds=60),
        raising=False,
    )
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(("question", "expected_experts"), QUESTIONS)
async def test_mocked_ask_uses_committed_active_corpora_and_returns_real_references(
    question: str,
    expected_experts: list[str],
) -> None:
    client = MockNugenClient()
    repository = MemoryRepository()
    interactions = MemoryInteractionRepository()
    app.dependency_overrides[ask_api.get_ask_client] = lambda: client
    app.dependency_overrides[ask_api.get_settings] = lambda: Settings(
        _env_file=None,
        nugen_test_model_id="fixture-base-model",
    )
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_source_content_store] = MemoryContentStore
    app.dependency_overrides[get_alignment_job_repository] = MemoryAlignmentRepository
    app.dependency_overrides[get_interaction_repository] = lambda: interactions

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.post("/api/ask", json={"question": question})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["routing"]["experts"] == expected_experts
    assert [result["expert"] for result in payload["experts"]] == expected_experts
    if expected_experts:
        assert payload["final_answer_source_grounding_supported"] is True
        assert payload["sources"]
        assert all(result["source_grounding_supported"] for result in payload["experts"])
        assert all(result["model_id"] == "fixture-base-model" for result in payload["experts"])
        assert all(result["evidence"] for result in payload["experts"])
        assert all(source["source"]["url"].startswith("https://") for source in payload["sources"])
        if question.startswith("How do income tax slabs"):
            assert any(
                "OLD-REGIME ORDINARY SLABS" in source["excerpt"]
                or "NEW-REGIME ORDINARY SLABS" in source["excerpt"]
                for source in payload["sources"]
            )
            assert "incometax.gov.in" in payload["sources"][0]["source"]["url"]
        elif question == "What is a SIP?":
            assert any("Systematic Investment Plan" in source["excerpt"] for source in payload["sources"])
            assert any("investor.sebi.gov.in" in source["source"]["url"] for source in payload["sources"])
        elif "waiting period" in question:
            assert any("waiting period" in source["excerpt"].casefold() for source in payload["sources"])
            assert any("irdai.gov.in" in source["source"]["url"] for source in payload["sources"])
        else:
            assert {source["expert"] for source in payload["sources"]} == {"tax", "investment"}
    else:
        assert payload["experts"] == []
        assert payload["sources"] == []
        assert payload["final_answer_source_grounding_supported"] is False
        assert len(client.completion_requests) == 1
    assert interactions.queries
