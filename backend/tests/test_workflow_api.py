from datetime import date, datetime, timezone
from typing import Any, List

import httpx
import pytest

from backend.app.main import app
from backend.app.nugen.alignment_service import NugenAlignmentService
from backend.app.nugen.client import NugenUploadFile
from backend.app.nugen.errors import (
    NugenAuthenticationError,
    NugenProtocolError,
    NugenServerError,
)
from backend.app.nugen.schemas import (
    AlignedModelSummary,
    AlignedModelsResponse,
    AlignmentStatusResponse,
    BaseModelSummary,
    BaseModelsResponse,
    CreateAlignmentRequest,
    CreateAlignmentResponse,
    DeploymentStatusResponse,
    DocumentStatusResponse,
    DocumentSummary,
    DocumentCategory,
    DocumentsResponse,
    UploadedDocumentsResponse,
)
from backend.app.alignment_jobs import get_alignment_job_repository
from backend.app.schemas import (
    ActiveSourceSelection,
    AlignmentJobRecord,
    ExpertName,
    SourceDocumentRecord,
)
from backend.app.source_content_store import get_source_content_store
from backend.app.source_documents import (
    ActiveSourceConflictError,
    SourceDocumentRepository,
    SourceDocumentStoreError,
)
from backend.app.workflow_api import get_alignment_service, get_document_repository
from backend.app.workflow_api import _validate_official_source


class MemoryDocumentRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], SourceDocumentRecord] = {}
        self.active_selections: dict[ExpertName, ActiveSourceSelection] = {}

    async def check_ready(self) -> None:
        return None

    async def save(self, record: SourceDocumentRecord) -> None:
        self.records[(record.domain, record.document_id)] = record

    async def get(self, domain: ExpertName, document_id: str) -> SourceDocumentRecord | None:
        return self.records.get((domain, document_id))

    async def list(
        self,
        domain: ExpertName,
        *,
        limit: int,
        offset: int,
    ) -> list[SourceDocumentRecord]:
        rows = [record for (record_domain, _), record in self.records.items() if record_domain == domain]
        rows.sort(key=lambda record: record.uploaded_at, reverse=True)
        return rows[offset : offset + limit]

    async def update_status(
        self,
        domain: ExpertName,
        document_id: str,
        status: str,
        checked_at: str,
    ) -> None:
        record = self.records[(domain, document_id)]
        self.records[(domain, document_id)] = record.model_copy(
            update={
                "nugen_status": status,
                "status_checked_at": datetime.fromisoformat(checked_at),
            }
        )

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
        current = self.active_selections.get(domain)
        if (current.revision if current else None) != expected_revision:
            raise ActiveSourceConflictError(
                "The active-source selection changed during validation; refresh and retry."
            )
        selection = ActiveSourceSelection(
            domain=domain,
            active_document_ids=document_ids,
            previous_document_ids=current.active_document_ids if current else [],
            revision=(expected_revision or 0) + 1,
            changed_at=changed_at,
        )
        self.active_selections[domain] = selection
        return selection


class MemorySourceContentStore:
    def __init__(self) -> None:
        self.contents: dict[tuple[ExpertName, str], bytes] = {}

    async def save(self, domain: ExpertName, document_id: str, content: bytes) -> None:
        self.contents[(domain, document_id)] = content

    async def get(self, domain: ExpertName, document_id: str) -> bytes | None:
        return self.contents.get((domain, document_id))


class MemoryAlignmentJobRepository:
    def __init__(self) -> None:
        self.records: dict[str, AlignmentJobRecord] = {}

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


class FakeAlignmentService:
    def __init__(self) -> None:
        self.upload_calls: list[tuple[list[NugenUploadFile], list[str] | None, list[str] | None]] = []
        self.base_model_calls: list[dict[str, Any]] = []
        self.document_list_calls: list[dict[str, Any]] = []
        self.document_statuses: dict[str, DocumentStatusResponse] = {}
        self.alignment_submit_calls = 0
        self.upload_error: Exception | None = None
        self.alignment_error: Exception | None = None
        self.alignment_status_error: Exception | None = None
        self.alignment_create_response = CreateAlignmentResponse(
            alignment_id="fixture-alignment-id",
            status="PROCESSING",
        )
        self.alignment_statuses: dict[str, AlignmentStatusResponse] = {}
        self.aligned_model_rows: list[AlignedModelSummary] = []
        self.model_deployment_statuses: dict[str, DeploymentStatusResponse] = {}
        self.deployment_calls: list[str] = []

    async def base_models(self, **kwargs: Any) -> BaseModelsResponse:
        self.base_model_calls.append(kwargs)
        return BaseModelsResponse(
            models=[
                BaseModelSummary(
                    model_id="fixture-base-model",
                    model_name="Fixture base model",
                    type="text-generation",
                    alignment_ready=True,
                    inference_ready=False,
                    available_on_request=False,
                ),
                BaseModelSummary(
                    model_id="not-alignment-ready",
                    model_name="Unsupported fixture model",
                    type="text-generation",
                    alignment_ready=False,
                    inference_ready=False,
                    available_on_request=False,
                ),
                BaseModelSummary(
                    model_id="request-only-model",
                    model_name="Access request fixture model",
                    type="text-generation",
                    alignment_ready=True,
                    inference_ready=False,
                    available_on_request=True,
                ),
            ]
        )

    async def upload_documents(
        self,
        files: list[NugenUploadFile],
        *,
        categories: list[str] | None = None,
        names: list[str] | None = None,
    ) -> UploadedDocumentsResponse:
        if self.upload_error:
            raise self.upload_error
        self.upload_calls.append((files, categories, names))
        return UploadedDocumentsResponse(document_ids=["fixture-document-id"])

    async def list_documents(self, **kwargs: Any) -> DocumentsResponse:
        self.document_list_calls.append(kwargs)
        return DocumentsResponse(
            documents=[
                DocumentSummary(
                    document_id="fixture-document-id",
                    document_name="Fixture source",
                    status="READY",
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    categories=["finpilot-indian-tax-education"],
                ),
                DocumentSummary(
                    document_id="other-domain-document",
                    document_name="Other expert source",
                    status="READY",
                    created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                    categories=["finpilot-investment-education"],
                ),
                DocumentSummary(
                    document_id="unregistered-document",
                    document_name="Unregistered tax source",
                    status="READY",
                    created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
                    categories=["finpilot-indian-tax-education"],
                ),
            ],
            categories=[DocumentCategory(name="finpilot-indian-tax-education", count=1)],
        )

    async def document_status(self, document_id: str) -> DocumentStatusResponse:
        return self.document_statuses.get(
            document_id,
            DocumentStatusResponse(status="PROCESSING", document_id=None),
        )

    async def create_alignment(self, request: CreateAlignmentRequest) -> CreateAlignmentResponse:
        self.alignment_submit_calls += 1
        if self.alignment_error is not None:
            raise self.alignment_error
        return self.alignment_create_response

    async def alignment_status(self, alignment_id: str) -> AlignmentStatusResponse:
        if self.alignment_status_error is not None:
            raise self.alignment_status_error
        return self.alignment_statuses.get(
            alignment_id,
            AlignmentStatusResponse(alignment_id=alignment_id, status="PROCESSING"),
        )

    async def aligned_models(self, **kwargs: Any) -> AlignedModelsResponse:
        return AlignedModelsResponse(domain_aligned_models=self.aligned_model_rows)

    async def deployment_status(self, model_id: str) -> DeploymentStatusResponse:
        return self.model_deployment_statuses.get(
            model_id,
            DeploymentStatusResponse(model_id=model_id, status="UNDEPLOYED"),
        )

    async def deploy_model(self, model_id: str, *, early: bool = False) -> str:
        self.deployment_calls.append(model_id)
        self.model_deployment_statuses[model_id] = DeploymentStatusResponse(
            model_id=model_id,
            status="DEPLOYED",
        )
        self.aligned_model_rows = [
            row.model_copy(update={"deployment_status": "DEPLOYED"})
            if row.model_id == model_id
            else row
            for row in self.aligned_model_rows
        ]
        return model_id


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def override_dependencies(
    service: FakeAlignmentService,
    repository: MemoryDocumentRepository,
    job_repository: MemoryAlignmentJobRepository | None = None,
) -> MemorySourceContentStore:
    content_store = MemorySourceContentStore()
    alignment_jobs = job_repository or MemoryAlignmentJobRepository()
    app.dependency_overrides[get_alignment_service] = lambda: service  # type: ignore[assignment]
    app.dependency_overrides[get_document_repository] = lambda: repository  # type: ignore[assignment]
    app.dependency_overrides[get_source_content_store] = lambda: content_store  # type: ignore[assignment]
    app.dependency_overrides[get_alignment_job_repository] = lambda: alignment_jobs  # type: ignore[assignment]
    return content_store


async def make_client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def official_record(
    document_id: str = "fixture-document-id",
    domain: ExpertName = "tax",
) -> SourceDocumentRecord:
    return SourceDocumentRecord(
        document_id=document_id,
        domain=domain,
        document_name="Income Tax Department guidance",
        original_filename="tax-guide.txt",
        source_url="https://www.incometax.gov.in/iec/foportal/",
        retrieved_on=date(2026, 10, 15),
        uploaded_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
        size_bytes=5000,
    )


def valid_corpus_text(domain: ExpertName, *, words: int = 1_600) -> bytes:
    sources = {
        "tax": ("https://www.incometax.gov.in/iec/foportal/", "Income Tax Department"),
        "investment": ("https://investor.sebi.gov.in/", "SEBI"),
        "insurance": ("https://irdai.gov.in/policyholders", "IRDAI"),
    }
    source_url, publisher = sources[domain]
    text = (
        f"# {domain.title()} source\n"
        f"Source: {source_url}\n"
        "Accessed: 2026-10-17\n\n"
        f"1. {domain.upper()} SOURCE SUMMARY\n"
        f"Source: {source_url}\n"
        "Accessed: 2026-10-17\n"
        f"Source title: Official {domain} education\n"
        f"Publisher: {publisher}\n"
        + (f"General {domain} education information. " * words)
    )
    return text.encode("utf-8")


@pytest.mark.parametrize(
    ("domain", "source_url"),
    [
        ("tax", "https://www.incometax.gov.in/iec/foportal/"),
        ("investment", "https://investor.sebi.gov.in/"),
        ("insurance", "https://irdai.gov.in/policyholders"),
        ("insurance", "https://policyholder.gov.in/"),
    ],
)
def test_official_source_allowlist_accepts_authority_hosts(
    domain: ExpertName,
    source_url: str,
) -> None:
    assert _validate_official_source(domain, source_url) == source_url


def test_official_source_allowlist_rejects_host_suffix_spoofing() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        _validate_official_source("investment", "https://investor.sebi.gov.in.attacker.example/")

    assert raised.value.status_code == 422


@pytest.mark.asyncio
async def test_lists_supported_base_models_and_domain_documents() -> None:
    service = FakeAlignmentService()
    repository = MemoryDocumentRepository()
    await repository.save(official_record())
    override_dependencies(service, repository)

    async with await make_client() as client:
        models_response = await client.get("/api/nugen/base-models")
        documents_response = await client.get("/api/documents?domain=tax&limit=20&offset=0")
        provider_documents_response = await client.get(
            "/api/nugen/documents?domain=tax&status=READY&limit=20&offset=0"
        )

    assert models_response.status_code == 200
    assert [model["model_id"] for model in models_response.json()["models"]] == [
        "fixture-base-model"
    ]
    assert service.base_model_calls == [{"alignment_ready": True, "limit": 100, "offset": 0}]
    assert documents_response.status_code == 200
    assert documents_response.json()["documents"][0]["document_id"] == "fixture-document-id"
    assert provider_documents_response.status_code == 200
    assert [document["document_id"] for document in provider_documents_response.json()["documents"]] == [
        "fixture-document-id"
    ]
    assert provider_documents_response.json()["documents"][0]["nugen_status"] == "READY"
    assert service.document_list_calls == [{"status": "READY", "limit": 20, "offset": 0}]


@pytest.mark.asyncio
async def test_uploads_valid_source_and_keeps_provenance_in_one_domain_set() -> None:
    service = FakeAlignmentService()
    repository = MemoryDocumentRepository()
    content_store = override_dependencies(service, repository)
    content = ("Official Income Tax Department education material. " * 100).encode()

    async with await make_client() as client:
        response = await client.post(
            "/api/documents",
            data={
                "domain": "tax",
                "source_url": "https://www.incometax.gov.in/iec/foportal/",
                "retrieved_on": "2026-10-14",
                "document_name": "Income tax guide",
            },
            files={"file": ("guide.txt", content, "text/plain")},
        )
        tax_list = await client.get("/api/documents?domain=tax")
        investment_list = await client.get("/api/documents?domain=investment")

    assert response.status_code == 200
    assert response.json()["document_id"] == "fixture-document-id"
    assert response.json()["source_url"] == "https://www.incometax.gov.in/iec/foportal/"
    assert response.json()["retrieved_on"] == "2026-10-14"
    assert response.json()["nugen_status"] is None
    assert tax_list.json()["documents"][0]["domain"] == "tax"
    assert investment_list.json()["documents"] == []
    assert service.upload_calls[0][1] == ["finpilot-indian-tax-education"]
    assert service.upload_calls[0][2] == ["Income tax guide"]
    assert content_store.contents[("tax", "fixture-document-id")] == content


@pytest.mark.asyncio
async def test_provenance_save_failure_after_provider_upload_warns_before_retry() -> None:
    class FailingRepository(MemoryDocumentRepository):
        async def save(self, record: SourceDocumentRecord) -> None:
            raise SourceDocumentStoreError("safe internal failure")

    service = FakeAlignmentService()
    repository = FailingRepository()
    override_dependencies(service, repository)
    content = ("Official Income Tax Department education material. " * 100).encode()

    async with await make_client() as client:
        response = await client.post(
            "/api/documents",
            data={
                "domain": "tax",
                "source_url": "https://www.incometax.gov.in/iec/foportal/",
                "document_name": "Income tax guide",
            },
            files={"file": ("guide.txt", content, "text/plain")},
        )

    assert response.status_code == 503
    assert "fixture-document-id" in response.json()["detail"]
    assert "do not upload it again blindly" in response.json()["detail"]
    assert service.upload_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "source_url", "body", "expected_status"),
    [
        (
            "guide.pdf",
            "https://www.incometax.gov.in/iec/foportal/",
            b"x " * 700,
            415,
        ),
        (
            "guide.txt",
            "https://example.com/unofficial",
            ("word " * 600).encode(),
            422,
        ),
        (
            "guide.txt",
            "https://www.incometax.gov.in/iec/foportal/",
            b"too short",
            422,
        ),
    ],
)
async def test_rejects_unsupported_document_inputs_before_upload(
    filename: str,
    source_url: str,
    body: bytes,
    expected_status: int,
) -> None:
    service = FakeAlignmentService()
    repository = MemoryDocumentRepository()
    override_dependencies(service, repository)

    async with await make_client() as client:
        response = await client.post(
            "/api/documents",
            data={"domain": "tax", "source_url": source_url},
            files={"file": (filename, body, "text/plain")},
        )

    assert response.status_code == expected_status
    assert service.upload_calls == []


@pytest.mark.asyncio
async def test_rejects_over_limit_document_size(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app import workflow_api

    service = FakeAlignmentService()
    repository = MemoryDocumentRepository()
    override_dependencies(service, repository)
    monkeypatch.setattr(workflow_api, "MAX_DOCUMENT_BYTES", 10)

    async with await make_client() as client:
        response = await client.post(
            "/api/documents",
            data={"domain": "tax", "source_url": "https://incometax.gov.in/"},
            files={"file": ("guide.txt", b"x" * 11, "text/plain")},
        )

    assert response.status_code == 413
    assert service.upload_calls == []


@pytest.mark.asyncio
async def test_upload_provider_failure_is_returned_without_credentials() -> None:
    service = FakeAlignmentService()
    service.upload_error = NugenAuthenticationError(
        "Nugen authentication failed for POST /api/v3/documents/create (HTTP 401); check NUGEN_API_KEY."
    )
    repository = MemoryDocumentRepository()
    override_dependencies(service, repository)

    async with await make_client() as client:
        response = await client.post(
            "/api/documents",
            data={
                "domain": "investment",
                "source_url": "https://investor.sebi.gov.in/",
            },
            files={"file": ("investor.txt", ("SEBI education material " * 200).encode(), "text/plain")},
        )

    assert response.status_code == 502
    assert "NUGEN_API_KEY" in response.json()["detail"]
    assert "mock-secret" not in response.text
    assert "Authorization" not in response.text


@pytest.mark.asyncio
async def test_document_status_refreshes_last_known_status() -> None:
    service = FakeAlignmentService()
    service.document_statuses["fixture-document-id"] = DocumentStatusResponse(
        status="READY",
        document_id="fixture-document-id",
    )
    repository = MemoryDocumentRepository()
    await repository.save(official_record())
    override_dependencies(service, repository)

    async with await make_client() as client:
        response = await client.get("/api/documents/tax/fixture-document-id/status")

    assert response.status_code == 200
    assert response.json()["provider_status"] == "READY"
    assert response.json()["document"]["nugen_status"] == "READY"
    assert response.json()["document"]["status_checked_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["tax", "investment", "insurance"])
async def test_domain_active_source_cutover_and_rollback_preserve_both_documents(
    domain: ExpertName,
) -> None:
    service = FakeAlignmentService()
    repository = MemoryDocumentRepository()
    content_store = override_dependencies(service, repository)
    old_id = (
        "document_01m4zddgyc6jbwcn"
        if domain == "tax"
        else f"fixture-old-{domain}-document"
    )
    new_id = f"fixture-new-{domain}-document"
    old_bytes = valid_corpus_text(domain, words=1_600)
    new_bytes = valid_corpus_text(domain, words=1_700)
    source_urls = {
        "tax": "https://www.incometax.gov.in/iec/foportal/",
        "investment": "https://investor.sebi.gov.in/",
        "insurance": "https://irdai.gov.in/policyholders",
    }
    filename = f"{domain}_expert.txt"
    old_record = official_record(old_id, domain).model_copy(
        update={
            "size_bytes": len(old_bytes),
            "original_filename": filename,
            "source_url": source_urls[domain],
        }
    )
    new_record = official_record(new_id, domain).model_copy(
        update={
            "size_bytes": len(new_bytes),
            "original_filename": filename,
            "source_url": source_urls[domain],
        }
    )
    await repository.save(old_record)
    await repository.save(new_record)
    content_store.contents[(domain, old_id)] = old_bytes
    content_store.contents[(domain, new_id)] = new_bytes
    service.document_statuses[old_id] = DocumentStatusResponse(
        status="READY",
        document_id=old_id,
    )
    service.document_statuses[new_id] = DocumentStatusResponse(
        status="READY",
        document_id=new_id,
    )

    async with await make_client() as client:
        baseline = await client.put(
            f"/api/documents/{domain}/active-source",
            json={"document_ids": [old_id]},
        )
        cutover = await client.put(
            f"/api/documents/{domain}/active-source",
            json={"document_ids": [new_id]},
        )
        active = await client.get(f"/api/documents/{domain}/active-source")
        rollback = await client.post(
            f"/api/documents/{domain}/active-source/rollback"
        )

    assert baseline.status_code == 200
    assert baseline.json()["active_document_ids"] == [old_id]
    assert baseline.json()["previous_document_ids"] == []
    assert cutover.status_code == 200
    assert cutover.json()["active_document_ids"] == [new_id]
    assert cutover.json()["previous_document_ids"] == [old_id]
    assert active.json()["active_document_ids"] == [new_id]
    assert rollback.status_code == 200
    assert rollback.json()["active_document_ids"] == [old_id]
    assert rollback.json()["previous_document_ids"] == [new_id]
    preserved_old = await repository.get(domain, old_id)
    preserved_new = await repository.get(domain, new_id)
    assert preserved_old is not None and preserved_new is not None
    for preserved, original in (
        (preserved_old, old_record),
        (preserved_new, new_record),
    ):
        assert preserved.document_id == original.document_id
        assert preserved.domain == original.domain
        assert preserved.document_name == original.document_name
        assert preserved.original_filename == original.original_filename
        assert preserved.source_url == original.source_url
        assert preserved.retrieved_on == original.retrieved_on
        assert preserved.uploaded_at == original.uploaded_at
        assert preserved.size_bytes == original.size_bytes
    assert service.alignment_submit_calls == 0
    assert service.deployment_calls == []
    assert content_store.contents[(domain, old_id)] == old_bytes
    assert content_store.contents[(domain, new_id)] == new_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["instruction heading", "missing header", "under 500 words"])
async def test_active_source_rejects_corpus_quality_failures_without_switching(failure: str) -> None:
    service = FakeAlignmentService()
    repository = MemoryDocumentRepository()
    content_store = override_dependencies(service, repository)
    old_id = "valid-tax-document"
    bad_id = f"bad-tax-document-{failure.replace(' ', '-')}"
    old_bytes = valid_corpus_text("tax", words=1_600)
    if failure == "instruction heading":
        bad_bytes = valid_corpus_text("tax", words=1_600).replace(
            b"1. TAX SOURCE SUMMARY",
            b"1. HOW FINPILOT SHOULD ANSWER TAX QUESTIONS",
        )
    elif failure == "missing header":
        bad_bytes = ("Tax education. " * 600).encode("utf-8")
    else:
        bad_bytes = (
            "Source: https://www.incometax.gov.in/\n"
            "Accessed: 2026-10-17\n"
            + "tax education " * 240
        ).encode("utf-8")

    for document_id, content in ((old_id, old_bytes), (bad_id, bad_bytes)):
        record = official_record(document_id, "tax").model_copy(
            update={"size_bytes": len(content), "original_filename": "tax-guide.txt"}
        )
        await repository.save(record)
        content_store.contents[("tax", document_id)] = content
        service.document_statuses[document_id] = DocumentStatusResponse(
            status="READY",
            document_id=document_id,
        )

    async with await make_client() as client:
        first = await client.put(
            "/api/documents/tax/active-source",
            json={"document_ids": [old_id]},
        )
        rejected = await client.put(
            "/api/documents/tax/active-source",
            json={"document_ids": [bad_id]},
        )

    assert first.status_code == 200
    assert rejected.status_code == 409
    assert "failed corpus quality checks" in rejected.json()["detail"]
    active = await repository.get_active_source_selection("tax")
    assert active is not None and active.active_document_ids == [old_id]


@pytest.mark.asyncio
async def test_source_health_reports_ready_invalid_and_missing_domains() -> None:
    service = FakeAlignmentService()
    repository = MemoryDocumentRepository()
    content_store = override_dependencies(service, repository)
    good_id = "good-tax-source"
    bad_id = "bad-investment-source"
    good_content = valid_corpus_text("tax", words=1_600)
    bad_content = valid_corpus_text("investment", words=1_600).replace(
        b"1. INVESTMENT SOURCE SUMMARY",
        b"1. DOCUMENT MAINTENANCE",
    )
    good = official_record(good_id, "tax").model_copy(
        update={"size_bytes": len(good_content), "original_filename": "tax-guide.txt"}
    )
    bad = official_record(bad_id, "investment").model_copy(
        update={
            "size_bytes": len(bad_content),
            "original_filename": "investment-guide.txt",
            "source_url": "https://investor.sebi.gov.in/",
        }
    )
    await repository.save(good)
    await repository.save(bad)
    content_store.contents[("tax", good_id)] = good_content
    content_store.contents[("investment", bad_id)] = bad_content
    repository.active_selections["tax"] = ActiveSourceSelection(
        domain="tax",
        active_document_ids=[good_id],
        revision=1,
        changed_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
    )
    repository.active_selections["investment"] = ActiveSourceSelection(
        domain="investment",
        active_document_ids=[bad_id],
        revision=1,
        changed_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
    )

    async with await make_client() as client:
        response = await client.get("/api/source-health")

    assert response.status_code == 200
    states = {item["domain"]: item for item in response.json()["domains"]}
    assert states["tax"]["status"] == "ready"
    assert states["investment"]["status"] == "invalid"
    assert "register and activate" in states["investment"]["message"]
    assert states["insurance"]["status"] == "missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["tax", "investment", "insurance"])
async def test_active_source_rejects_unready_or_unmatched_content_without_switching(
    domain: ExpertName,
) -> None:
    service = FakeAlignmentService()
    repository = MemoryDocumentRepository()
    content_store = override_dependencies(service, repository)
    old_id = f"old-{domain}-document"
    new_id = f"new-{domain}-document"
    old_bytes = valid_corpus_text(domain, words=1_600)
    new_bytes = valid_corpus_text(domain, words=1_700)
    filename = f"{domain}_expert.txt"
    old_record = official_record(old_id, domain).model_copy(
        update={"size_bytes": len(old_bytes), "original_filename": filename}
    )
    new_record = official_record(new_id, domain).model_copy(
        update={"size_bytes": len(new_bytes), "original_filename": filename}
    )
    await repository.save(old_record)
    await repository.save(new_record)
    content_store.contents[(domain, old_id)] = old_bytes
    content_store.contents[(domain, new_id)] = new_bytes[:-1]
    service.document_statuses[old_id] = DocumentStatusResponse(
        status="READY",
        document_id=old_id,
    )
    service.document_statuses[new_id] = DocumentStatusResponse(
        status="READY",
        document_id=new_id,
    )
    repository.active_selections[domain] = ActiveSourceSelection(
        domain=domain,
        active_document_ids=[old_id],
        revision=1,
        changed_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
    )

    async with await make_client() as client:
        response = await client.put(
            f"/api/documents/{domain}/active-source",
            json={"document_ids": [new_id]},
        )

    assert response.status_code == 409
    assert "size does not match" in response.json()["detail"]
    active = await repository.get_active_source_selection(domain)
    assert active is not None and active.active_document_ids == [old_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["tax", "investment", "insurance"])
async def test_active_source_rejects_provider_processing_document_without_switching(
    domain: ExpertName,
) -> None:
    service = FakeAlignmentService()
    repository = MemoryDocumentRepository()
    content_store = override_dependencies(service, repository)
    old_id = f"old-{domain}-document"
    new_id = f"processing-{domain}-document"
    old_bytes = f"Original {domain} source. ".encode() * 40
    new_bytes = f"New {domain} source still processing. ".encode() * 40
    filename = f"{domain}_expert.txt"
    old_record = official_record(old_id, domain).model_copy(
        update={"size_bytes": len(old_bytes), "original_filename": filename}
    )
    new_record = official_record(new_id, domain).model_copy(
        update={"size_bytes": len(new_bytes), "original_filename": filename}
    )
    await repository.save(old_record)
    await repository.save(new_record)
    content_store.contents[(domain, old_id)] = old_bytes
    content_store.contents[(domain, new_id)] = new_bytes
    repository.active_selections[domain] = ActiveSourceSelection(
        domain=domain,
        active_document_ids=[old_id],
        revision=1,
        changed_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
    )
    service.document_statuses[new_id] = DocumentStatusResponse(
        status="PROCESSING",
        document_id=new_id,
    )

    async with await make_client() as client:
        response = await client.put(
            f"/api/documents/{domain}/active-source",
            json={"document_ids": [new_id]},
        )

    assert response.status_code == 409
    assert "not READY" in response.json()["detail"]
    active = await repository.get_active_source_selection(domain)
    assert active is not None and active.active_document_ids == [old_id]


@pytest.mark.asyncio
async def test_alignment_prepare_returns_review_payload_without_submitting() -> None:
    service = FakeAlignmentService()
    service.document_statuses["fixture-document-id"] = DocumentStatusResponse(
        status="READY",
        document_id="fixture-document-id",
    )
    repository = MemoryDocumentRepository()
    await repository.save(official_record())
    override_dependencies(service, repository)

    async with await make_client() as client:
        response = await client.post(
            "/api/alignments/prepare",
            json={
                "domain": "tax",
                "alignment_name": "Indian Tax Education Expert",
                "base_model_id": "fixture-base-model",
                "document_ids": ["fixture-document-id"],
                "description": "Educational responses grounded in Income Tax Department sources.",
            },
        )

    assert response.status_code == 200
    result = response.json()
    assert result["submitted"] is False
    assert result["documents"][0]["document_id"] == "fixture-document-id"
    assert result["request_payload"] == {
        "alignment_name": "Indian Tax Education Expert",
        "base_model_id": "fixture-base-model",
        "document_ids": ["fixture-document-id"],
        "description": "Educational responses grounded in Income Tax Department sources.",
    }
    assert service.alignment_submit_calls == 0


@pytest.mark.asyncio
async def test_alignment_prepare_rejects_unready_documents_and_unknown_models() -> None:
    service = FakeAlignmentService()
    repository = MemoryDocumentRepository()
    await repository.save(official_record())
    override_dependencies(service, repository)
    payload = {
        "domain": "tax",
        "alignment_name": "Tax expert preview",
        "base_model_id": "fixture-base-model",
        "document_ids": ["fixture-document-id"],
    }

    async with await make_client() as client:
        not_ready = await client.post("/api/alignments/prepare", json=payload)
        service.document_statuses["fixture-document-id"] = DocumentStatusResponse(
            status="READY",
            document_id="fixture-document-id",
        )
        unknown_model = await client.post(
            "/api/alignments/prepare",
            json={**payload, "base_model_id": "not-in-nugen-list"},
        )
        unavailable_model = await client.post(
            "/api/alignments/prepare",
            json={**payload, "base_model_id": "not-alignment-ready"},
        )

    assert not_ready.status_code == 409
    assert "PROCESSING" in not_ready.json()["detail"]
    assert unknown_model.status_code == 422
    assert "not returned" in unknown_model.json()["detail"]
    assert unavailable_model.status_code == 422
    assert "alignment_ready" in unavailable_model.json()["detail"]
    assert service.alignment_submit_calls == 0


def alignment_submission_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "domain": "tax",
        "alignment_name": "Indian Tax Education Expert",
        "base_model_id": "fixture-base-model",
        "document_ids": ["fixture-document-id"],
        "confirm_submission": True,
        **overrides,
    }


@pytest.mark.asyncio
async def test_explicit_alignment_submission_persists_pending_provider_job() -> None:
    service = FakeAlignmentService()
    service.document_statuses["fixture-document-id"] = DocumentStatusResponse(
        status="READY",
        document_id="fixture-document-id",
    )
    repository = MemoryDocumentRepository()
    await repository.save(official_record())
    jobs = MemoryAlignmentJobRepository()
    override_dependencies(service, repository, jobs)

    async with await make_client() as client:
        response = await client.post(
            "/api/alignments/submit",
            json=alignment_submission_payload(),
        )
        status_response = await client.get(
            f"/api/alignments/{service.alignment_create_response.alignment_id}/status"
        )

    assert response.status_code == 202
    created = response.json()
    assert created["alignment_id"] == "fixture-alignment-id"
    assert created["provider_status"] == "PROCESSING"
    assert created["alignment_status"] == "unaligned"
    assert created["inference_ready"] is False
    assert created["document_ids"] == ["fixture-document-id"]
    assert service.alignment_submit_calls == 1
    assert status_response.status_code == 200
    assert status_response.json()["provider_status"] == "PROCESSING"
    assert status_response.json()["verification_state"] == "PENDING"


@pytest.mark.asyncio
async def test_alignment_is_not_reported_until_ready_model_is_deployed_and_confirmed() -> None:
    service = FakeAlignmentService()
    service.document_statuses["fixture-document-id"] = DocumentStatusResponse(
        status="READY",
        document_id="fixture-document-id",
    )
    service.alignment_statuses["fixture-alignment-id"] = AlignmentStatusResponse(
        alignment_id="fixture-alignment-id",
        status="READY",
    )
    service.aligned_model_rows = [
        AlignedModelSummary(
            model_id="fixture-domain-model",
            model_name="Fixture tax model",
            deployment_status="UNDEPLOYED",
            base_model_id="fixture-base-model",
            alignment_id="fixture-alignment-id",
            created_at="2026-10-15T00:00:00Z",
        )
    ]
    repository = MemoryDocumentRepository()
    await repository.save(official_record())
    jobs = MemoryAlignmentJobRepository()
    override_dependencies(service, repository, jobs)

    async with await make_client() as client:
        submitted = await client.post(
            "/api/alignments/submit",
            json=alignment_submission_payload(),
        )
        before_deploy = await client.get("/api/alignments/fixture-alignment-id/status")
        deployed = await client.post(
            "/api/alignments/fixture-alignment-id/deploy",
            json={"confirm_deployment": True},
        )

    assert submitted.status_code == 202
    assert before_deploy.status_code == 200
    before = before_deploy.json()
    assert before["provider_status"] == "READY"
    assert before["model_id"] == "fixture-domain-model"
    assert before["alignment_status"] == "unaligned"
    assert before["verification_state"] == "READY_NOT_DEPLOYED"
    assert before["inference_ready"] is False
    assert deployed.status_code == 200
    after = deployed.json()
    assert after["alignment_status"] == "aligned"
    assert after["verification_state"] == "VERIFIED"
    assert after["inference_ready"] is True
    assert after["deployment_status"] == "DEPLOYED"
    assert service.deployment_calls == ["fixture-domain-model"]


@pytest.mark.asyncio
async def test_failed_alignment_persists_provider_state_and_failure_note() -> None:
    service = FakeAlignmentService()
    service.document_statuses["fixture-document-id"] = DocumentStatusResponse(
        status="READY",
        document_id="fixture-document-id",
    )
    service.alignment_statuses["fixture-alignment-id"] = AlignmentStatusResponse(
        alignment_id="fixture-alignment-id",
        status="FAILED",
    )
    repository = MemoryDocumentRepository()
    await repository.save(official_record())
    jobs = MemoryAlignmentJobRepository()
    override_dependencies(service, repository, jobs)

    async with await make_client() as client:
        submitted = await client.post(
            "/api/alignments/submit",
            json=alignment_submission_payload(),
        )
        status = await client.get("/api/alignments/fixture-alignment-id/status")

    assert submitted.status_code == 202
    result = status.json()
    assert result["provider_status"] == "FAILED"
    assert result["verification_state"] == "FAILED"
    assert result["alignment_status"] == "unaligned"
    assert "does not include the alignment failure reason" in result["error_detail"]


@pytest.mark.asyncio
async def test_failed_refresh_invalidates_stale_deployment_state() -> None:
    service = FakeAlignmentService()
    service.document_statuses["fixture-document-id"] = DocumentStatusResponse(
        status="READY",
        document_id="fixture-document-id",
    )
    service.alignment_statuses["fixture-alignment-id"] = AlignmentStatusResponse(
        alignment_id="fixture-alignment-id",
        status="READY",
    )
    service.aligned_model_rows = [
        AlignedModelSummary(
            model_id="fixture-domain-model",
            model_name="Fixture tax model",
            deployment_status="UNDEPLOYED",
            base_model_id="fixture-base-model",
            alignment_id="fixture-alignment-id",
            created_at="2026-10-15T00:00:00Z",
        )
    ]
    repository = MemoryDocumentRepository()
    await repository.save(official_record())
    jobs = MemoryAlignmentJobRepository()
    override_dependencies(service, repository, jobs)

    async with await make_client() as client:
        await client.post("/api/alignments/submit", json=alignment_submission_payload())
        previously_ready = await client.get("/api/alignments/fixture-alignment-id/status")
        assert previously_ready.json()["deployment_status"] == "UNDEPLOYED"

        service.alignment_status_error = NugenServerError(
            "Nugen returned a server error.", status_code=503
        )
        failed_refresh = await client.post(
            "/api/alignments/fixture-alignment-id/deploy",
            json={"confirm_deployment": True},
        )

    assert "not confirmed" in failed_refresh.json()["detail"]
    result = await jobs.get_by_alignment_id("fixture-alignment-id")
    assert failed_refresh.status_code == 409
    assert result is not None
    assert result.provider_status is None
    assert result.model_id is None
    assert result.deployment_status is None
    assert result.inference_ready is False
    assert service.deployment_calls == []


@pytest.mark.asyncio
async def test_ready_alignment_without_provider_model_id_remains_unverified() -> None:
    service = FakeAlignmentService()
    service.document_statuses["fixture-document-id"] = DocumentStatusResponse(
        status="READY",
        document_id="fixture-document-id",
    )
    service.alignment_statuses["fixture-alignment-id"] = AlignmentStatusResponse(
        alignment_id="fixture-alignment-id",
        status="READY",
    )
    repository = MemoryDocumentRepository()
    await repository.save(official_record())
    jobs = MemoryAlignmentJobRepository()
    override_dependencies(service, repository, jobs)

    async with await make_client() as client:
        await client.post("/api/alignments/submit", json=alignment_submission_payload())
        response = await client.get("/api/alignments/fixture-alignment-id/status")

    result = response.json()
    assert response.status_code == 200
    assert result["provider_status"] == "READY"
    assert result["model_id"] is None
    assert result["alignment_status"] == "unaligned"
    assert result["verification_state"] == "UNVERIFIED"
    assert "No aligned model row" in result["status_note"]


@pytest.mark.asyncio
async def test_missing_model_id_and_missing_submission_confirmation_do_not_submit() -> None:
    service = FakeAlignmentService()
    service.document_statuses["fixture-document-id"] = DocumentStatusResponse(
        status="READY",
        document_id="fixture-document-id",
    )
    repository = MemoryDocumentRepository()
    await repository.save(official_record())
    override_dependencies(service, repository)

    async with await make_client() as client:
        missing_confirmation = await client.post(
            "/api/alignments/submit",
            json=alignment_submission_payload().copy() | {"confirm_submission": False},
        )
        missing_model = await client.post(
            "/api/alignments/submit",
            json=alignment_submission_payload().copy() | {"base_model_id": ""},
        )

    assert missing_confirmation.status_code == 422
    assert missing_model.status_code == 422
    assert service.alignment_submit_calls == 0


@pytest.mark.asyncio
async def test_malformed_status_response_stays_unverified() -> None:
    service = FakeAlignmentService()
    service.document_statuses["fixture-document-id"] = DocumentStatusResponse(
        status="READY",
        document_id="fixture-document-id",
    )
    service.alignment_statuses["fixture-alignment-id"] = AlignmentStatusResponse(
        alignment_id="another-job",
        status="READY",
    )
    repository = MemoryDocumentRepository()
    await repository.save(official_record())
    jobs = MemoryAlignmentJobRepository()
    override_dependencies(service, repository, jobs)

    async with await make_client() as client:
        await client.post("/api/alignments/submit", json=alignment_submission_payload())
        response = await client.get("/api/alignments/fixture-alignment-id/status")

    assert response.status_code == 200
    assert response.json()["alignment_status"] == "unaligned"
    assert response.json()["verification_state"] == "UNVERIFIED"
    assert "different alignment ID" in response.json()["error_detail"]


@pytest.mark.asyncio
async def test_submission_protocol_failure_is_persisted_as_unknown_not_retried() -> None:
    service = FakeAlignmentService()
    service.alignment_error = NugenProtocolError("Nugen response did not match schema.")
    service.document_statuses["fixture-document-id"] = DocumentStatusResponse(
        status="READY",
        document_id="fixture-document-id",
    )
    repository = MemoryDocumentRepository()
    await repository.save(official_record())
    jobs = MemoryAlignmentJobRepository()
    override_dependencies(service, repository, jobs)

    async with await make_client() as client:
        response = await client.post(
            "/api/alignments/submit",
            json=alignment_submission_payload(),
        )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["submission_state"] == "SUBMISSION_UNKNOWN"
    assert detail["tracking_id"] in jobs.records
    assert service.alignment_submit_calls == 1
