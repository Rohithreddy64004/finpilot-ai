import asyncio
from datetime import date
from typing import Any

import httpx
import pytest

from backend.app import ask_api
from backend.app.alignment_jobs import get_alignment_job_repository
from backend.app.config import Settings
from backend.app.inference_api import get_claim_verifier
from backend.app.rate_limit import InMemoryRateLimiter
from backend.app.interactions import get_interaction_repository
from backend.app.main import app
from backend.app.router import RouterDecision
from backend.app.schemas import (
    AskExpertResult,
    ClaimVerification,
    DomainInferenceResponse,
    RetrievedEvidence,
    SourceReference,
)
from backend.app.source_content_store import get_source_content_store
from backend.app.workflow_api import get_document_repository
from backend.app.schemas import QueryLogRecord


class NoopClient:
    async def aclose(self) -> None:
        return None


class FakeInteractionRepository:
    def __init__(self) -> None:
        self.queries: dict[str, QueryLogRecord] = {}

    async def save_query(self, record: QueryLogRecord) -> None:
        self.queries[record.id] = record

    async def get_query(self, query_id: str):
        return self.queries.get(query_id)

    async def save_feedback(self, record) -> None:
        raise AssertionError("feedback is not part of an ask request")

    async def feedback_summary(self):
        raise AssertionError("summary is not part of an ask request")

    async def save_verified_correction(self, record) -> None:
        raise AssertionError("corrections are not part of an ask request")

    async def count_verified_corrections(self) -> int:
        return 0


def domain_response(
    domain: str,
    *,
    answer: str | None = None,
    citation_id: str = "S1",
    grounded: bool = True,
) -> DomainInferenceResponse:
    source = SourceReference(
        title=f"{domain.title()} education source",
        publisher="Public authority",
        url=f"https://example.org/{domain}",
        accessed_on=date(2026, 10, 17),
    )
    return DomainInferenceResponse(
        domain=domain,
        model_id="base-model",
        alignment_status="unaligned",
        answer=answer or f"{domain.title()} evidence supports this point. [{citation_id}]",
        evidence=[
            RetrievedEvidence(
                citation_id=citation_id,
                passage_id=f"{domain}:passage-1",
                document_id=f"{domain}-doc",
                document_name=f"{domain.title()} source",
                excerpt=f"{domain.title()} evidence supports this point.",
                source=source,
            )
        ],
        source_references=[source],
        source_grounding_supported=grounded,
        source_grounding_note=(
            "The answer cites a retrieved passage." if grounded else "No passage supports the answer."
        ),
        claim_verifications=[
            ClaimVerification(
                claim_text=f"{domain.title()} evidence supports this point.",
                status="unverified",
                assessment_basis="unavailable",
                evidence_ids=[],
                cited_evidence_ids=[citation_id],
            )
        ],
        claim_verification_status="unverified",
        independent_verification_performed=False,
        claim_verification_note="No independent verifier is configured.",
        applicable_as_of=date(2026, 10, 17),
    )


@pytest.fixture
def install_ask_dependencies(monkeypatch: pytest.MonkeyPatch):
    settings = Settings(_env_file=None, ask_expert_timeout_seconds=1.0)
    interaction_repository = FakeInteractionRepository()
    monkeypatch.setattr(
        app.state,
        "api_rate_limiter",
        InMemoryRateLimiter(max_requests=10_000, window_seconds=60),
        raising=False,
    )
    monkeypatch.setitem(app.dependency_overrides, ask_api.get_ask_client, lambda: NoopClient())
    monkeypatch.setitem(app.dependency_overrides, ask_api.get_settings, lambda: settings)
    monkeypatch.setitem(app.dependency_overrides, get_document_repository, lambda: object())
    monkeypatch.setitem(app.dependency_overrides, get_source_content_store, lambda: object())
    monkeypatch.setitem(app.dependency_overrides, get_alignment_job_repository, lambda: object())
    monkeypatch.setitem(
        app.dependency_overrides,
        get_interaction_repository,
        lambda: interaction_repository,
    )
    return settings


async def post_ask(payload: dict[str, Any]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/ask", json=payload)


@pytest.mark.asyncio
async def test_ask_routes_cross_domain_and_globally_remaps_citations(
    monkeypatch: pytest.MonkeyPatch,
    install_ask_dependencies,
) -> None:
    async def fake_route(question: str, **_: Any) -> RouterDecision:
        return RouterDecision(
            experts=["investment", "insurance"],
            reason="The question spans investment and insurance education.",
            routing_method="model",
        )

    async def fake_run(request, **_: Any):
        return domain_response(request.domain)

    async def fake_synthesize(question, experts, sources, **_: Any) -> str:
        assert [source.citation_id for source in sources] == ["S1", "S2"]
        return "Investment features can vary [S1], and insurance terms can vary [S2]."

    monkeypatch.setattr(ask_api, "route", fake_route)
    monkeypatch.setattr(ask_api, "run_domain_inference", fake_run)
    monkeypatch.setattr(ask_api, "synthesize", fake_synthesize)

    response = await post_ask({"question": "Compare investments and insurance."})

    assert response.status_code == 200
    payload = response.json()
    assert payload["routing"] == {
        "experts": ["investment", "insurance"],
        "reason": "The question spans investment and insurance education.",
        "routing_method": "model",
    }
    assert [item["expert"] for item in payload["experts"]] == ["investment", "insurance"]
    assert [source["citation_id"] for source in payload["sources"]] == ["S1", "S2"]
    assert payload["experts"][0]["evidence"][0]["citation_id"] == "S1"
    assert payload["experts"][1]["evidence"][0]["citation_id"] == "S2"
    assert "[S1]" in payload["final_answer"] and "[S2]" in payload["final_answer"]
    assert payload["final_answer_source_grounding_supported"] is True
    assert payload["final_answer_claim_verification_status"] == "verification_unavailable"
    assert payload["final_answer_independent_verification_performed"] is False


@pytest.mark.asyncio
async def test_ask_skips_router_and_synthesis_for_one_explicit_expert(
    monkeypatch: pytest.MonkeyPatch,
    install_ask_dependencies,
) -> None:
    async def should_not_route(*args: Any, **kwargs: Any):
        raise AssertionError("explicit expert override must skip model routing")

    async def fake_run(request, **_: Any):
        return domain_response(request.domain)

    async def should_not_synthesize(*args: Any, **kwargs: Any):
        raise AssertionError("a single expert answer should not be synthesized")

    monkeypatch.setattr(ask_api, "route", should_not_route)
    monkeypatch.setattr(ask_api, "run_domain_inference", fake_run)
    monkeypatch.setattr(ask_api, "synthesize", should_not_synthesize)

    response = await post_ask({"question": "Explain investment risk.", "experts": ["investment"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["routing"]["routing_method"] == "override"
    assert payload["experts"][0]["expert"] == "investment"
    assert payload["final_answer"] == "Investment evidence supports this point. [S1]"
    assert payload["latency_ms"]["synthesis_ms"] == 0
    saved_queries = app.dependency_overrides[get_interaction_repository]().queries
    assert len(saved_queries) == 1
    saved_query = next(iter(saved_queries.values()))
    assert saved_query.question == "Explain investment risk."
    assert saved_query.routing.routing_method == "override"


@pytest.mark.asyncio
async def test_ask_out_of_scope_does_not_call_any_expert(
    monkeypatch: pytest.MonkeyPatch,
    install_ask_dependencies,
) -> None:
    async def fake_route(question: str, **_: Any) -> RouterDecision:
        return RouterDecision(
            experts=[],
            reason="Out of scope: this is a weather question.",
            routing_method="model",
        )

    async def should_not_run(*args: Any, **kwargs: Any):
        raise AssertionError("out-of-scope question called an expert")

    monkeypatch.setattr(ask_api, "route", fake_route)
    monkeypatch.setattr(ask_api, "run_domain_inference", should_not_run)

    response = await post_ask({"question": "What is tomorrow's weather?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["experts"] == []
    assert payload["sources"] == []
    assert "out of scope" in payload["final_answer"].casefold()
    assert payload["latency_ms"]["experts_ms"] == 0


@pytest.mark.asyncio
async def test_one_expert_failure_does_not_fail_others(
    monkeypatch: pytest.MonkeyPatch,
    install_ask_dependencies,
) -> None:
    async def fake_route(question: str, **_: Any) -> RouterDecision:
        return RouterDecision(
            experts=["tax", "investment"],
            reason="Cross-domain finance question.",
            routing_method="model",
        )

    async def fake_run(request, **_: Any):
        if request.domain == "tax":
            raise RuntimeError("private details must not leak")
        return domain_response(request.domain)

    monkeypatch.setattr(ask_api, "route", fake_route)
    monkeypatch.setattr(ask_api, "run_domain_inference", fake_run)

    response = await post_ask({"question": "Compare tax and investments."})

    assert response.status_code == 200
    payload = response.json()
    assert [item["status"] for item in payload["experts"]] == ["error", "complete"]
    assert "private details" not in response.text
    assert "tax expert failed unexpectedly" in payload["experts"][0]["error"].casefold()
    assert "investment" in payload["final_answer"].casefold()


@pytest.mark.asyncio
async def test_ask_experts_run_concurrently(
    monkeypatch: pytest.MonkeyPatch,
    install_ask_dependencies,
) -> None:
    started = 0
    both_started = asyncio.Event()

    async def fake_route(question: str, **_: Any) -> RouterDecision:
        return RouterDecision(
            experts=["tax", "insurance"],
            reason="Question spans two domains.",
            routing_method="model",
        )

    async def fake_run(request, **_: Any):
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.5)
        return domain_response(request.domain)

    async def fake_synthesize(question, experts, sources, **_: Any) -> str:
        return "The experts address different topics [S1] and [S2]."

    monkeypatch.setattr(ask_api, "route", fake_route)
    monkeypatch.setattr(ask_api, "run_domain_inference", fake_run)
    monkeypatch.setattr(ask_api, "synthesize", fake_synthesize)

    response = await post_ask({"question": "Compare tax and insurance."})

    assert response.status_code == 200
    assert started == 2
    assert all(item["status"] == "complete" for item in response.json()["experts"])


@pytest.mark.asyncio
async def test_ask_expert_timeout_returns_an_error_state(
    monkeypatch: pytest.MonkeyPatch,
    install_ask_dependencies,
) -> None:
    install_ask_dependencies.ask_expert_timeout_seconds = 0.01

    async def fake_run(request, **_: Any):
        await asyncio.sleep(0.05)
        return domain_response(request.domain)

    monkeypatch.setattr(ask_api, "run_domain_inference", fake_run)

    response = await post_ask({"question": "Explain insurance terms.", "experts": ["insurance"]})

    assert response.status_code == 200
    assert response.json()["experts"][0]["status"] == "error"
    assert "timeout" in response.json()["experts"][0]["error"].casefold()


@pytest.mark.asyncio
async def test_ask_withholds_synthesis_with_unknown_citation(
    monkeypatch: pytest.MonkeyPatch,
    install_ask_dependencies,
) -> None:
    async def fake_route(question: str, **_: Any) -> RouterDecision:
        return RouterDecision(
            experts=["tax", "insurance"],
            reason="Question spans two domains.",
            routing_method="model",
        )

    async def fake_run(request, **_: Any):
        return domain_response(request.domain)

    async def fake_synthesize(question, experts, sources, **_: Any) -> str:
        return "An unsupported statement [S99]."

    monkeypatch.setattr(ask_api, "route", fake_route)
    monkeypatch.setattr(ask_api, "run_domain_inference", fake_run)
    monkeypatch.setattr(ask_api, "synthesize", fake_synthesize)

    response = await post_ask({"question": "Compare tax and insurance."})

    payload = response.json()
    assert payload["final_answer_source_grounding_supported"] is False
    assert "withheld" in payload["final_answer"].casefold()
    assert any("not returned" in item for item in payload["gaps"])


@pytest.mark.asyncio
async def test_ask_synthesis_failure_returns_individual_expert_answers(
    monkeypatch: pytest.MonkeyPatch,
    install_ask_dependencies,
) -> None:
    async def fake_route(question: str, **_: Any) -> RouterDecision:
        return RouterDecision(
            experts=["tax", "investment"],
            reason="Question spans two domains.",
            routing_method="model",
        )

    async def fake_run(request, **_: Any):
        return domain_response(request.domain)

    async def failing_synthesis(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("safe mocked failure")

    monkeypatch.setattr(ask_api, "route", fake_route)
    monkeypatch.setattr(ask_api, "run_domain_inference", fake_run)
    monkeypatch.setattr(ask_api, "synthesize", failing_synthesis)

    response = await post_ask({"question": "Compare tax and investment rules."})

    payload = response.json()
    assert payload["final_answer_source_grounding_supported"] is True
    assert "Tax evidence" in payload["final_answer"]
    assert "Investment evidence" in payload["final_answer"]
    assert "Synthesis was unavailable" in payload["final_answer"]
    assert "[S1]" in payload["final_answer"] and "[S2]" in payload["final_answer"]


@pytest.mark.asyncio
async def test_ask_rejects_invalid_question_domains_and_duplicate_overrides(
    install_ask_dependencies,
) -> None:
    assert (await post_ask({"question": "   "})).status_code == 422
    assert (await post_ask({"question": "x" * 2_001})).status_code == 422
    assert (await post_ask({"question": "Question", "experts": ["weather"]})).status_code == 422
    assert (await post_ask({"question": "Question", "experts": ["tax", "tax"]})).status_code == 422


@pytest.mark.asyncio
async def test_ask_rate_limit_returns_429_before_starting_an_expert(
    monkeypatch: pytest.MonkeyPatch,
    install_ask_dependencies,
) -> None:
    from backend.app import ask_api

    monkeypatch.setattr(
        app.state,
        "api_rate_limiter",
        InMemoryRateLimiter(max_requests=1, window_seconds=60),
        raising=False,
    )
    calls = 0

    async def fake_run(request, **_: Any):
        nonlocal calls
        calls += 1
        return domain_response(request.domain)

    monkeypatch.setattr(ask_api, "run_domain_inference", fake_run)
    payload = {"question": "Explain investment risk.", "experts": ["investment"]}
    assert (await post_ask(payload)).status_code == 200
    limited = await post_ask(payload)
    assert limited.status_code == 429
    assert limited.headers["retry-after"]
    assert calls == 1
