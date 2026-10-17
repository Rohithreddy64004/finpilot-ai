from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from backend.app.config import Settings, get_settings
from backend.app.interactions import get_interaction_repository
from backend.app.main import app
from backend.app.schemas import (
    AskRoutingDecision,
    FeedbackRecord,
    FeedbackSummaryResponse,
    QueryLogRecord,
    VerifiedCorrectionRecord,
)


class FakeInteractionRepository:
    def __init__(self) -> None:
        self.queries: dict[str, QueryLogRecord] = {}
        self.feedback: list[FeedbackRecord] = []
        self.corrections: list[VerifiedCorrectionRecord] = []

    async def save_query(self, record: QueryLogRecord) -> None:
        self.queries[record.id] = record

    async def get_query(self, query_id: str) -> QueryLogRecord | None:
        return self.queries.get(query_id)

    async def save_feedback(self, record: FeedbackRecord) -> None:
        self.feedback.append(record)

    async def feedback_summary(self) -> FeedbackSummaryResponse:
        expert_counts: dict[str, int] = {}
        for record in self.feedback:
            for expert in record.correct_experts or []:
                expert_counts[expert] = expert_counts.get(expert, 0) + 1
        return FeedbackSummaryResponse(
            total=len(self.feedback),
            thumbs_up=sum(record.rating == "up" for record in self.feedback),
            thumbs_down=sum(record.rating == "down" for record in self.feedback),
            correct_expert_counts=expert_counts,
            verified_correction_count=len(self.corrections),
        )

    async def save_verified_correction(self, record: VerifiedCorrectionRecord) -> None:
        self.corrections.append(record)

    async def count_verified_corrections(self) -> int:
        return len(self.corrections)


@pytest.fixture
def install_repository(monkeypatch: pytest.MonkeyPatch):
    repository = FakeInteractionRepository()
    repository.queries["query-1"] = QueryLogRecord(
        id="query-1",
        question="Explain investment risk.",
        routing=AskRoutingDecision(
            experts=["investment"],
            reason="Investment topic.",
            routing_method="fallback",
        ),
        timestamp=datetime.now(timezone.utc),
    )
    monkeypatch.setitem(app.dependency_overrides, get_interaction_repository, lambda: repository)
    monkeypatch.setenv("FINPILOT_CORRECTION_REVIEW_TOKEN", "test-review-token")
    settings = Settings(_env_file=None)
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: settings)
    return repository


async def request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.asyncio
async def test_feedback_is_stored_separately_from_verified_corrections(install_repository):
    repository = install_repository
    response = await request(
        "POST",
        "/api/feedback",
        json={
            "query_id": "query-1",
            "rating": "down",
            "comment": "The router missed an expert.",
            "correct_experts": ["tax"],
        },
    )

    assert response.status_code == 201
    assert len(repository.feedback) == 1
    assert repository.corrections == []
    assert "not treated as a verified correction" in response.json()["notice"]


@pytest.mark.asyncio
async def test_feedback_requires_a_known_query(install_repository):
    response = await request(
        "POST", "/api/feedback", json={"query_id": "missing", "rating": "up"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_feedback_summary_exposes_aggregate_counts_only(install_repository):
    await request("POST", "/api/feedback", json={"query_id": "query-1", "rating": "up"})
    response = await request("GET", "/api/feedback/summary")

    assert response.status_code == 200
    assert response.json() == {
        "total": 1,
        "thumbs_up": 1,
        "thumbs_down": 0,
        "correct_expert_counts": {},
        "verified_correction_count": 0,
    }
    assert "comment" not in response.json()


@pytest.mark.asyncio
async def test_verified_correction_requires_reviewer_authorization(install_repository):
    payload = {
        "query_id": "query-1",
        "correction": "This reviewed text corrects the previous answer.",
        "verified_by": "Reviewer One",
        "verification_note": "Checked against official source text and date.",
        "source_references": [
            {"title": "Official source", "url": "https://example.gov.in/guide"}
        ],
    }
    denied = await request("POST", "/api/verified-corrections", json=payload)
    assert denied.status_code == 403

    accepted = await request(
        "POST",
        "/api/verified-corrections",
        json=payload,
        headers={"Authorization": "Bearer test-review-token"},
    )
    assert accepted.status_code == 201
    assert len(install_repository.corrections) == 1
    assert install_repository.corrections[0].query_id == "query-1"


@pytest.mark.asyncio
async def test_verified_correction_rejects_non_https_source(install_repository):
    payload = {
        "query_id": "query-1",
        "correction": "A reviewed correction.",
        "verified_by": "Reviewer One",
        "verification_note": "Checked against official source text and date.",
        "source_references": [{"title": "Untrusted", "url": "http://example.org"}],
    }
    response = await request(
        "POST",
        "/api/verified-corrections",
        json=payload,
        headers={"Authorization": "Bearer test-review-token"},
    )
    assert response.status_code == 422
    assert install_repository.corrections == []
