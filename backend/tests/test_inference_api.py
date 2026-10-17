from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, List

import httpx
import pytest

from backend.app.config import Settings
from backend.app.main import app
from backend.app.nugen.claim_verifier import NugenClaimVerifier
from backend.app.nugen.errors import NugenRateLimitError, NugenTimeoutError
from backend.app.nugen.schemas import (
    AlignedModelSummary,
    AlignedModelsResponse,
    AlignmentStatusResponse,
    BaseModelSummary,
    BaseModelsResponse,
    ChatCompletionResponse,
    CompletionChoice,
    CompletionMessage,
    DocumentStatusResponse,
    DeploymentStatusResponse,
)
from backend.app.alignment_jobs import get_alignment_job_repository
from backend.app.schemas import (
    ActiveSourceSelection,
    AlignmentJobRecord,
    ExpertName,
    SourceDocumentRecord,
)
from backend.app.inference_api import (
    get_claim_verifier,
    get_inference_client,
    get_inference_settings,
)
from backend.app.source_content_store import get_source_content_store
from backend.app.workflow_api import get_document_repository


class MemoryDocumentRepository:
    def __init__(
        self,
        records: list[SourceDocumentRecord] | None = None,
        *,
        active_document_ids: dict[ExpertName, list[str]] | None = None,
    ) -> None:
        self.records = records or []
        selected = active_document_ids
        if selected is None:
            # Existing inference fixtures explicitly seed their previous corpus
            # as active. Tests for the fail-closed path pass an empty mapping.
            selected = {
                domain: [row.document_id for row in self.records if row.domain == domain]
                for domain in ("tax", "investment", "insurance")
            }
        self.active_selections: dict[ExpertName, ActiveSourceSelection] = {
            domain: ActiveSourceSelection(
                domain=domain,
                active_document_ids=document_ids,
                revision=1,
                changed_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
            )
            for domain, document_ids in selected.items()
            if document_ids
        }

    async def check_ready(self) -> None:
        return None

    async def save(self, record: SourceDocumentRecord) -> None:
        self.records.append(record)

    async def get(self, domain: ExpertName, document_id: str) -> SourceDocumentRecord | None:
        return next(
            (
                item
                for item in self.records
                if item.domain == domain and item.document_id == document_id
            ),
            None,
        )

    async def list(
        self,
        domain: ExpertName,
        *,
        limit: int,
        offset: int,
    ) -> list[SourceDocumentRecord]:
        matching = [item for item in self.records if item.domain == domain]
        return matching[offset : offset + limit]

    async def update_status(
        self,
        domain: ExpertName,
        document_id: str,
        status: str,
        checked_at: str,
    ) -> None:
        return None

    async def get_active_source_selection(
        self,
        domain: ExpertName,
    ) -> ActiveSourceSelection | None:
        return self.active_selections.get(domain)

    async def activate_source_selection(
        self,
        domain: ExpertName,
        document_ids: List[str],
        *,
        expected_revision: int | None,
        changed_at: datetime,
    ) -> ActiveSourceSelection:
        from backend.app.source_documents import ActiveSourceConflictError

        current = self.active_selections.get(domain)
        if (current.revision if current else None) != expected_revision:
            raise ActiveSourceConflictError("The active-source selection changed during validation; refresh and retry.")
        selection = ActiveSourceSelection(
            domain=domain,
            active_document_ids=document_ids,
            previous_document_ids=current.active_document_ids if current else [],
            revision=(expected_revision or 0) + 1,
            changed_at=changed_at,
        )
        self.active_selections[domain] = selection
        return selection


class MemoryAlignmentJobRepository:
    def __init__(self, records: list[AlignmentJobRecord] | None = None) -> None:
        self.records = {row.tracking_id: row for row in records or []}

    async def save(self, record: AlignmentJobRecord) -> None:
        self.records[record.tracking_id] = record

    async def get_by_tracking_id(self, tracking_id: str) -> AlignmentJobRecord | None:
        return self.records.get(tracking_id)

    async def get_by_alignment_id(self, alignment_id: str) -> AlignmentJobRecord | None:
        return next(
            (row for row in self.records.values() if row.alignment_id == alignment_id),
            None,
        )

    async def find_domain_model(
        self,
        domain: ExpertName,
        model_id: str,
    ) -> AlignmentJobRecord | None:
        return next(
            (
                row
                for row in self.records.values()
                if row.domain == domain and row.model_id == model_id
            ),
            None,
        )


class FakeNugenClient:
    def __init__(self) -> None:
        self.document_statuses: dict[str, str] = {}
        self.status_calls: list[str] = []
        self.base_model_rows = [
            BaseModelSummary(
                model_id="fixture-base-model",
                model_name="Fixture base model",
                type="text-generation",
                inference_ready=True,
            )
        ]
        self.aligned_model_rows: list[AlignedModelSummary] = []
        self.alignment_statuses: dict[str, AlignmentStatusResponse] = {}
        self.deployment_statuses: dict[str, DeploymentStatusResponse] = {}
        self.completion_error: Exception | None = None
        self.completion_text = "A cautious educational explanation. [S1]"
        self.completion_request: Any = None
        self.completion_calls = 0

    async def get_document_status(self, document_id: str) -> DocumentStatusResponse:
        self.status_calls.append(document_id)
        return DocumentStatusResponse(
            document_id=document_id,
            status=self.document_statuses.get(document_id, "READY"),
        )

    async def list_base_models(self, **kwargs: Any) -> BaseModelsResponse:
        assert kwargs == {
            "inference_ready": True,
            "model_type": "text-generation",
            "limit": 100,
            "offset": 0,
        }
        return BaseModelsResponse(models=self.base_model_rows)

    async def list_aligned_models(self, **kwargs: Any) -> AlignedModelsResponse:
        assert kwargs == {
            "deployment_status": None,
            "base_model_id": None,
            "limit": 100,
            "offset": 0,
        }
        return AlignedModelsResponse(domain_aligned_models=self.aligned_model_rows)

    async def get_alignment_status(self, alignment_id: str) -> AlignmentStatusResponse:
        return self.alignment_statuses.get(
            alignment_id,
            AlignmentStatusResponse(alignment_id=alignment_id, status="PROCESSING"),
        )

    async def get_deployment_status(self, model_id: str) -> DeploymentStatusResponse:
        return self.deployment_statuses.get(
            model_id,
            DeploymentStatusResponse(model_id=model_id, status="UNDEPLOYED"),
        )

    async def chat_completions(self, request: Any) -> ChatCompletionResponse:
        self.completion_calls += 1
        self.completion_request = request
        if self.completion_error is not None:
            raise self.completion_error
        return ChatCompletionResponse(
            id="fixture-completion",
            object="chat.completion",
            created=1_704_123_600,
            model=request.model,
            choices=[
                CompletionChoice(
                    index=0,
                    message=CompletionMessage(
                        role="assistant",
                        content=self.completion_text,
                    ),
                    finish_reason="stop",
                )
            ],
        )


class EmptyContentStore:
    async def get(self, domain: ExpertName, document_id: str) -> bytes | None:
        return None


class FixedContentStore:
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def get(self, domain: ExpertName, document_id: str) -> bytes | None:
        return self.content if domain == "tax" else None


class StaticClaimVerifier:
    def __init__(self, result: Any) -> None:
        self.result = result

    async def assess(self, claim: str, passages: list[Any]) -> Any:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def source_record(domain: ExpertName, document_id: str) -> SourceDocumentRecord:
    urls = {
        "tax": "https://www.incometax.gov.in/iec/foportal/",
        "investment": "https://investor.sebi.gov.in/",
        "insurance": "https://irdai.gov.in/policyholders",
    }
    filename = f"{domain}_expert.txt"
    corpus_path = Path(__file__).resolve().parents[2] / "knowledge_bases" / filename
    return SourceDocumentRecord(
        document_id=document_id,
        domain=domain,
        document_name=f"{domain.title()} education source",
        original_filename=filename,
        source_url=urls[domain],
        retrieved_on=date(2026, 10, 15),
        uploaded_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
        size_bytes=corpus_path.stat().st_size,
    )


@pytest.fixture(autouse=True)
def clear_dependency_overrides(monkeypatch: pytest.MonkeyPatch):
    from backend.app.rate_limit import InMemoryRateLimiter

    app.dependency_overrides.clear()
    monkeypatch.setattr(
        app.state,
        "api_rate_limiter",
        InMemoryRateLimiter(max_requests=10_000, window_seconds=60),
        raising=False,
    )
    yield
    app.dependency_overrides.clear()


def setup_dependencies(
    *,
    client: FakeNugenClient | None = None,
    repository: MemoryDocumentRepository | None = None,
    settings: Settings | None = None,
    alignment_repository: MemoryAlignmentJobRepository | None = None,
    claim_verifier: Any | None = None,
) -> tuple[FakeNugenClient, MemoryDocumentRepository]:
    nugen = client or FakeNugenClient()
    documents = repository or MemoryDocumentRepository()
    app.dependency_overrides[get_inference_client] = lambda: nugen  # type: ignore[assignment]
    app.dependency_overrides[get_inference_settings] = lambda: settings or Settings(
        _env_file=None,
        nugen_api_key="fixture-secret",
        nugen_test_model_id="fixture-base-model",
    )
    app.dependency_overrides[get_document_repository] = lambda: documents  # type: ignore[assignment]
    app.dependency_overrides[get_source_content_store] = EmptyContentStore  # type: ignore[assignment]
    app.dependency_overrides[get_alignment_job_repository] = lambda: (
        alignment_repository or MemoryAlignmentJobRepository()
    )  # type: ignore[assignment]
    if claim_verifier is not None:
        app.dependency_overrides[get_claim_verifier] = lambda: claim_verifier  # type: ignore[assignment]
    return nugen, documents


async def make_client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["tax", "investment", "insurance"])
async def test_inference_uses_selected_domain_and_returns_passage_grounding(
    domain: ExpertName,
) -> None:
    nugen = FakeNugenClient()
    repository = MemoryDocumentRepository(
        [
            source_record("tax", "tax-source"),
            source_record("investment", "investment-source"),
            source_record("insurance", "insurance-source"),
        ]
    )
    setup_dependencies(client=nugen, repository=repository)

    questions = {
        "tax": "What is the difference between a deduction and a rebate?",
        "investment": "Does a systematic investment plan guarantee returns?",
        "insurance": "What are exclusions and waiting periods?",
    }

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": domain, "question": questions[domain]},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["domain"] == domain
    assert payload["model_id"] == "fixture-base-model"
    assert payload["alignment_status"] == "unaligned"
    assert payload["answer"] == "A cautious educational explanation. [S1]"
    assert payload["source_grounding_supported"] is True
    assert payload["claim_verification_status"] == "verification_unavailable"
    assert payload["claim_verifications"][0]["status"] == "unverified"
    assert payload["claim_verifications"][0]["assessment_basis"] == "source_text_comparison"
    assert payload["claim_verifications"][0]["cited_evidence_ids"] == ["S1"]
    assert payload["independent_verification_performed"] is False
    assert payload["evidence"][0]["document_id"] == f"{domain}-source"
    assert payload["evidence"][0]["citation_id"] == "S1"
    assert payload["source_references"][0]["url"] == {
        "tax": "https://www.incometax.gov.in/iec/foportal/",
        "investment": "https://investor.sebi.gov.in/",
        "insurance": "https://irdai.gov.in/policyholders",
    }[domain]
    assert not any("base model" in item or "general education" in item for item in payload["limitations"])
    assert nugen.status_calls == [f"{domain}-source"]
    assert nugen.completion_calls == 1
    system_message = nugen.completion_request.messages[0].content
    focus_phrases = {
        "tax": "Focus on Indian tax education",
        "investment": "Focus on investment education for India",
        "insurance": "Focus on insurance education for India",
    }
    assert focus_phrases[domain] in system_message
    assert "Use only the supplied source passages" in system_message
    assert "standard Markdown" in system_message
    assert "Do not repeat generic education or professional-advice disclaimers" in system_message
    assert "Never invent a marker" in system_message
    assert f"Document ID: {domain}-source" in system_message
    for other_domain in {"tax", "investment", "insurance"} - {domain}:
        assert f"Document ID: {other_domain}-source" not in system_message
    assert nugen.completion_request.messages[1].content == questions[domain]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"domain": "other", "question": "Question?"},
        {"domain": "tax"},
        {"domain": "tax", "question": "   "},
    ],
)
async def test_invalid_domain_or_missing_blank_question_returns_422(payload: dict[str, Any]) -> None:
    setup_dependencies(repository=MemoryDocumentRepository())

    async with await make_client() as client:
        response = await client.post("/api/inference", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_inference_requires_registered_documents_for_requested_domain() -> None:
    nugen = FakeNugenClient()
    repository = MemoryDocumentRepository([source_record("investment", "investment-source")])
    setup_dependencies(client=nugen, repository=repository)

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "tax", "question": "Tax question?"},
        )

    assert response.status_code == 409
    assert "No active source set is selected for tax" in response.json()["detail"]
    assert nugen.status_calls == []
    assert nugen.completion_calls == 0


@pytest.mark.asyncio
async def test_inference_stops_when_a_registered_domain_document_is_not_ready() -> None:
    nugen = FakeNugenClient()
    nugen.document_statuses["tax-source"] = "PROCESSING"
    repository = MemoryDocumentRepository([source_record("tax", "tax-source")])
    setup_dependencies(client=nugen, repository=repository)

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "tax", "question": "Tax question?"},
        )

    assert response.status_code == 409
    assert "Active tax source document 'tax-source' is PROCESSING" in response.json()["detail"]
    assert nugen.completion_calls == 0


@pytest.mark.asyncio
async def test_inference_fails_closed_when_documents_exist_but_no_active_set_is_selected() -> None:
    nugen = FakeNugenClient()
    repository = MemoryDocumentRepository(
        [source_record("tax", "tax-source")],
        active_document_ids={},
    )
    setup_dependencies(client=nugen, repository=repository)

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "tax", "question": "Tax question?"},
        )

    assert response.status_code == 409
    assert "explicitly activate" in response.json()["detail"]
    assert nugen.status_calls == []
    assert nugen.completion_calls == 0


@pytest.mark.asyncio
async def test_inference_retrieves_only_the_explicit_active_source_set() -> None:
    nugen = FakeNugenClient()
    repository = MemoryDocumentRepository(
        [
            source_record("tax", "old-tax-source"),
            source_record("tax", "new-tax-source"),
        ],
        active_document_ids={"tax": ["new-tax-source"]},
    )
    setup_dependencies(client=nugen, repository=repository)

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "tax", "question": "What is a rebate?"},
        )

    assert response.status_code == 200
    assert [item["document_id"] for item in response.json()["evidence"]] == [
        "new-tax-source"
    ]
    assert nugen.status_calls == ["new-tax-source"]
    assert "Document ID: old-tax-source" not in nugen.completion_request.messages[0].content


@pytest.mark.asyncio
async def test_inference_rejects_a_byte_count_mismatch_in_the_active_source() -> None:
    nugen = FakeNugenClient()
    mismatched = source_record("tax", "tax-source").model_copy(
        update={"size_bytes": Path(__file__).resolve().parents[2].joinpath("knowledge_bases", "tax_expert.txt").stat().st_size + 1}
    )
    repository = MemoryDocumentRepository([mismatched])
    setup_dependencies(client=nugen, repository=repository)

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "tax", "question": "What is a tax rebate?"},
        )

    assert response.status_code == 503
    assert "size does not match" in response.json()["detail"]
    assert nugen.completion_calls == 0


@pytest.mark.asyncio
async def test_empty_retrieval_returns_ungrounded_safe_answer_without_calling_model() -> None:
    nugen = FakeNugenClient()
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository([source_record("tax", "tax-source")]),
    )

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "tax", "question": "zebra quasar telescope"},
        )

    assert response.status_code == 200
    assert response.json()["source_grounding_supported"] is False
    assert response.json()["source_references"] == []
    assert "couldn't find relevant information" in response.json()["answer"]
    assert nugen.completion_calls == 0


@pytest.mark.asyncio
async def test_tax_slab_query_with_generic_passage_withholds_inference() -> None:
    nugen = FakeNugenClient()
    generic_content = (
        b"1. GENERAL TAX INFORMATION\n"
        b"Income tax is calculated using taxable income and may depend on applicable rules. "
        b"Individuals should check the current official source for details."
    )
    record = source_record("tax", "tax-generic").model_copy(
        update={"size_bytes": len(generic_content)}
    )
    store = FixedContentStore(generic_content)
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository([record]),
    )
    app.dependency_overrides[get_source_content_store] = lambda: store  # type: ignore[assignment]

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "tax", "question": "How do income tax slabs work for individuals?"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_grounding_supported"] is False
    assert payload["claim_verification_status"] == "not_performed"
    assert "do not contain both usable income thresholds and slab rates" in payload["answer"]
    assert "₹" not in payload["answer"]
    assert "%" not in payload["answer"]
    assert nugen.completion_calls == 0


@pytest.mark.asyncio
async def test_tax_slab_draft_with_unsupported_numeric_values_is_withheld() -> None:
    nugen = FakeNugenClient()
    nugen.completion_text = "A 99% rate applies above ₹99,99,999. [S1]"
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository([source_record("tax", "tax-source")]),
    )

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "tax", "question": "How do income tax slabs work for individuals?"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_grounding_supported"] is False
    assert payload["claim_verification_status"] == "not_performed"
    assert "99%" not in payload["answer"]
    assert "99,99,999" not in payload["answer"]
    assert "withheld" in payload["answer"]
    assert "OLD REGIME SLABS" in payload["evidence"][0]["excerpt"]
    assert nugen.completion_calls == 1
    system_message = nugen.completion_request.messages[0].content
    assert "Explain progressive taxation" in system_message
    assert "Distinguish assessment year from financial year" in system_message
    assert "preserve every supplied threshold and rate" in system_message
    assert nugen.completion_request.messages[1].content == (
        "How do income tax slabs work for individuals?"
    )


@pytest.mark.asyncio
async def test_source_date_mismatch_is_separate_from_grounding_and_claim_support() -> None:
    nugen = FakeNugenClient()
    nugen.completion_text = "An SIP does not guarantee returns. [S1]"
    old_source = source_record("investment", "investment-source").model_copy(
        update={"retrieved_on": date(2020, 1, 1)}
    )
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository([old_source]),
        claim_verifier=StaticClaimVerifier(
            {
                "status": "supported",
                "evidence": [
                    {
                        "evidence_id": "S1",
                        "quote": (
                            "an SIP does not protect the investor from market losses, "
                            "guarantee returns, or ensure that the chosen fund is appropriate."
                        ),
                    }
                ],
            }
        ),
    )

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={
                "domain": "investment",
                "question": "Does an SIP guarantee returns?",
                "applicable_as_of": "2026-10-15",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_grounding_supported"] is True
    assert payload["claim_verification_status"] == "fully_verified"
    assert payload["independent_verification_performed"] is True
    assert payload["claim_verifications"][0]["status"] == "supported"
    assert payload["claim_verifications"][0]["assessment_basis"] == "independent_verifier"
    assert payload["claim_verifications"][0]["evidence_ids"] == ["S1"]
    assert payload["answer"] == nugen.completion_text
    assert any(
        "retrieved on 2020-01-01" in item
        and "not the requested applicable date 2026-10-15" in item
        and "Check current official guidance" in item
        for item in payload["limitations"]
    )


@pytest.mark.asyncio
async def test_verifier_model_must_be_separate_from_answer_model() -> None:
    nugen = FakeNugenClient()
    verifier = NugenClaimVerifier(nugen, "fixture-base-model")
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository([source_record("tax", "tax-source")]),
        claim_verifier=verifier,
    )

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "tax", "question": "What is a rebate?"},
        )

    assert response.status_code == 200
    assert response.json()["claim_verification_status"] == "verification_unavailable"
    assert response.json()["independent_verification_performed"] is False
    assert "different model" in response.json()["claim_verification_note"]
    assert nugen.completion_calls == 1


@pytest.mark.asyncio
async def test_registered_document_without_a_matching_local_copy_is_unavailable() -> None:
    nugen = FakeNugenClient()
    wrong_file_record = source_record("tax", "tax-source").model_copy(
        update={"original_filename": "different-guide.txt"}
    )
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository([wrong_file_record]),
    )

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "tax", "question": "What is a deduction?"},
        )

    assert response.status_code == 503
    assert "Source content unavailable" in response.json()["detail"]
    assert "not present in the local content store" in response.json()["detail"]
    assert nugen.completion_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "completion_text",
    [
        "Every investor is guaranteed a profit [S9].",
        "A systematic investment plan guarantees a 20% annual return.",
    ],
)
async def test_unsupported_model_output_is_withheld(
    completion_text: str,
) -> None:
    nugen = FakeNugenClient()
    nugen.completion_text = completion_text
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository([source_record("investment", "investment-source")]),
    )

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "investment", "question": "Does an SIP guarantee returns?"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_grounding_supported"] is False
    assert completion_text.split(" [S9]")[0] not in payload["answer"]
    assert "verifiable citations" in payload["answer"]
    assert payload["evidence"][0]["source"]["url"] == "https://investor.sebi.gov.in/"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("completion_text", "expected_note"),
    [
        ("A statement without a marker.", "No valid citation markers were returned"),
        ("A statement with a malformed marker. [Sx]", "Malformed citation markers were returned"),
        ("A statement with an unknown marker. [S99]", "Unknown citation IDs were returned (S99)"),
    ],
)
async def test_citation_failure_notes_distinguish_missing_malformed_and_unknown(
    completion_text: str,
    expected_note: str,
) -> None:
    nugen = FakeNugenClient()
    nugen.completion_text = completion_text
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository(
            [source_record("investment", "investment-source")]
        ),
    )

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={
                "domain": "investment",
                "question": "Does a systematic investment plan guarantee returns?",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_grounding_supported"] is False
    assert expected_note in payload["source_grounding_note"]
    assert completion_text not in payload["source_grounding_note"]
    assert completion_text not in payload["answer"]


@pytest.mark.asyncio
async def test_selected_base_model_must_be_explicitly_inference_ready() -> None:
    nugen = FakeNugenClient()
    nugen.base_model_rows = [
        BaseModelSummary(
            model_id="fixture-base-model",
            model_name="Fixture base model",
            type="text-generation",
            inference_ready=False,
        )
    ]
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository([source_record("tax", "tax-source")]),
    )

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "tax", "question": "Tax question?"},
        )

    assert response.status_code == 422
    assert nugen.completion_calls == 0


@pytest.mark.asyncio
async def test_configured_aligned_model_is_used_only_when_nugen_confirms_deployment() -> None:
    nugen = FakeNugenClient()
    nugen.base_model_rows = []
    nugen.aligned_model_rows = [
        AlignedModelSummary(
            model_id="fixture-aligned-tax",
            model_name="Fixture aligned tax model",
            deployment_status="DEPLOYED",
            base_model_id="fixture-base-model",
            alignment_id="fixture-alignment",
            created_at="2026-10-15T00:00:00Z",
        )
    ]
    nugen.alignment_statuses["fixture-alignment"] = AlignmentStatusResponse(
        alignment_id="fixture-alignment",
        status="READY",
    )
    nugen.deployment_statuses["fixture-aligned-tax"] = DeploymentStatusResponse(
        model_id="fixture-aligned-tax",
        status="DEPLOYED",
    )
    alignment_repository = MemoryAlignmentJobRepository(
        [
            AlignmentJobRecord(
                tracking_id="fixture-tracking",
                domain="tax",
                alignment_name="Fixture tax model",
                base_model_id="fixture-base-model",
                document_ids=["tax-source"],
                alignment_id="fixture-alignment",
                submission_state="SUBMITTED",
                provider_status="READY",
                model_id="fixture-aligned-tax",
                listed_deployment_status="DEPLOYED",
                deployment_status="DEPLOYED",
                alignment_status="aligned",
                verification_state="VERIFIED",
                inference_ready=True,
                status_note="Previously provider verified.",
                created_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
                updated_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
            )
        ]
    )
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository([source_record("tax", "tax-source")]),
        settings=Settings(
            _env_file=None,
            nugen_api_key="fixture-secret",
            nugen_tax_model_id="fixture-aligned-tax",
        ),
        alignment_repository=alignment_repository,
    )

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={
                "domain": "tax",
                "question": "What is a tax rebate?",
                "model_id": "fixture-aligned-tax",
            },
        )

    assert response.status_code == 200
    assert response.json()["alignment_status"] == "aligned"
    assert response.json()["model_id"] == "fixture-aligned-tax"
    assert response.json()["source_grounding_supported"] is True
    assert nugen.completion_calls == 1
    assert not any("base model" in item for item in response.json()["limitations"])


@pytest.mark.asyncio
async def test_deployed_aligned_model_without_domain_job_record_is_rejected() -> None:
    nugen = FakeNugenClient()
    nugen.aligned_model_rows = [
        AlignedModelSummary(
            model_id="fixture-aligned-tax",
            model_name="Fixture aligned tax model",
            deployment_status="DEPLOYED",
            base_model_id="fixture-base-model",
            alignment_id="fixture-alignment",
            created_at="2026-10-15T00:00:00Z",
        )
    ]
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository([source_record("tax", "tax-source")]),
        settings=Settings(
            _env_file=None,
            nugen_api_key="fixture-secret",
            nugen_tax_model_id="fixture-aligned-tax",
            nugen_test_model_id="fixture-base-model",
        ),
    )

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "tax", "question": "What is a tax rebate?"},
        )

    assert response.status_code == 503
    assert "no locally tracked Nugen alignment job" in response.json()["detail"]
    assert nugen.completion_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "assessment", "expected_status", "expect_withheld"),
    [
        (
            "A Systematic Investment Plan (SIP) is a method of investing a fixed amount at regular intervals in a mutual fund scheme. [S1]",
            {
                "status": "supported",
                "evidence": [
                    {
                        "evidence_id": "S1",
                        "quote": "A Systematic Investment Plan (SIP) is a method of investing a fixed amount at regular intervals in a mutual fund scheme.",
                    }
                ],
            },
            "fully_verified",
            False,
        ),
        (
            "A systematic investment plan guarantees returns. [S1]",
            {
                "status": "contradicted",
                "evidence": [
                    {
                        "evidence_id": "S1",
                        "quote": "an SIP does not protect the investor from market losses, guarantee returns, or ensure that the chosen fund is appropriate.",
                    }
                ],
            },
            "contains_contradiction",
            True,
        ),
        (
            "Every investor will make a profit. [S1]",
            {"status": "unverified", "evidence": []},
            "unverified",
            False,
        ),
        (
            "A systematic investment plan guarantees returns. [S1]",
            RuntimeError("private verifier failure"),
            "unverified",
            True,
        ),
        (
            "A systematic investment plan guarantees returns. [S1]",
            {
                "status": "supported",
                "evidence": [{"evidence_id": "S99", "quote": "Unknown reference."}],
            },
            "unverified",
            True,
        ),
    ],
)
async def test_inference_keeps_claim_verification_separate_from_source_grounding(
    answer: str,
    assessment: Any,
    expected_status: str,
    expect_withheld: bool,
) -> None:
    nugen = FakeNugenClient()
    nugen.completion_text = answer
    verifier = StaticClaimVerifier(assessment)
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository(
            [source_record("investment", "investment-source")]
        ),
        claim_verifier=verifier,
    )

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={
                "domain": "investment",
                "question": "Does a systematic investment plan guarantee returns?",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_grounding_supported"] is True
    assert payload["claim_verification_status"] == expected_status
    assert payload["claim_verifications"][0]["claim_text"] == answer.split(" [S1]")[0]
    assert payload["claim_verifications"][0]["status"] == (
        "supported" if expected_status == "fully_verified" else
        "contradicted" if expected_status == "contains_contradiction" else
        "unverified"
    )
    if expect_withheld:
        assert "generated answer was withheld" in payload["answer"]
    else:
        assert payload["answer"] == nugen.completion_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(NugenRateLimitError("Nugen rate limit reached."), 429), (NugenTimeoutError("Nugen request timed out."), 504)],
)
async def test_nugen_errors_are_mapped_safely_without_secrets(
    error: Exception,
    expected_status: int,
) -> None:
    nugen = FakeNugenClient()
    nugen.completion_error = error
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository([source_record("tax", "tax-source")]),
    )

    async with await make_client() as client:
        response = await client.post(
            "/api/inference",
            json={"domain": "tax", "question": "What is a tax rebate?"},
        )

    assert response.status_code == expected_status
    assert "fixture-secret" not in response.text


@pytest.mark.asyncio
async def test_inference_rate_limit_returns_429_before_second_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.rate_limit import InMemoryRateLimiter

    nugen = FakeNugenClient()
    setup_dependencies(
        client=nugen,
        repository=MemoryDocumentRepository([source_record("tax", "tax-source")]),
    )
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60)
    monkeypatch.setattr(app.state, "api_rate_limiter", limiter, raising=False)
    payload = {"domain": "tax", "question": "What is a tax rebate?"}

    async with await make_client() as client:
        first = await client.post("/api/inference", json=payload)
        second = await client.post("/api/inference", json=payload)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers.get("retry-after")
    assert nugen.completion_calls == 1
