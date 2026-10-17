"""HTTP endpoints for preparing, uploading, and reviewing source documents."""

from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from backend.app.config import get_settings
from backend.app.corpus_quality import inspect_corpus_text
from backend.app.evidence_retrieval import (
    SourceContentUnavailableError,
    load_registered_document_text,
)
from backend.app.alignment_jobs import (
    AlignmentJobRepository,
    get_alignment_job_repository,
)
from backend.app.alignment_workflow import refresh_alignment_job
from backend.app.nugen.alignment_service import NugenAlignmentService
from backend.app.nugen.client import NugenClient, NugenUploadFile
from backend.app.nugen.errors import (
    NugenAPIError,
    NugenConfigurationError,
    NugenProtocolError,
    NugenTimeoutError,
    NugenTransportError,
)
from backend.app.nugen.schemas import (
    BaseModelsResponse,
    CreateAlignmentRequest,
    DocumentStatusValue,
)
from backend.app.source_content_store import (
    LocalSourceContentStore,
    SourceContentStoreError,
    get_source_content_store,
)
from backend.app.schemas import (
    ActiveSourceSelection,
    ActiveSourceUpdateRequest,
    AlignmentDeploymentRequest,
    AlignmentJobRecord,
    AlignmentPreviewRequest,
    AlignmentPreviewResponse,
    AlignmentSubmitRequest,
    DocumentStatusCheckResponse,
    ExpertName,
    SourceDocumentListResponse,
    SourceDocumentRecord,
    SourceDomainHealth,
    SourceHealthResponse,
)
from backend.app.source_documents import (
    ActiveSourceConflictError,
    MongoSourceDocumentRepository,
    SourceDocumentRepository,
    SourceDocumentStoreError,
)
from backend.app.source_diagnostics import diagnose_active_sources


router = APIRouter(prefix="/api", tags=["document preparation"])

MAX_DOCUMENT_BYTES = 100 * 1024 * 1024
MIN_DOCUMENT_WORDS = 500
ALLOWED_EXTENSIONS = {".txt", ".md"}
ALLOWED_MEDIA_TYPES = {"text/plain", "text/markdown", "application/octet-stream"}
DOMAIN_CATEGORIES: dict[ExpertName, str] = {
    "tax": "finpilot-indian-tax-education",
    "investment": "finpilot-investment-education",
    "insurance": "finpilot-insurance-education",
}
OFFICIAL_SOURCE_HOSTS: dict[ExpertName, tuple[str, ...]] = {
    "tax": ("incometax.gov.in", "incometaxindia.gov.in"),
    "investment": ("sebi.gov.in",),
    "insurance": ("irdai.gov.in", "policyholder.gov.in"),
}
_SOURCE_URL = TypeAdapter(AnyHttpUrl)


async def get_nugen_client() -> AsyncIterator[NugenClient]:
    async with NugenClient(get_settings()) as client:
        yield client


async def get_alignment_service(
    client: NugenClient = Depends(get_nugen_client),
) -> NugenAlignmentService:
    return NugenAlignmentService(client)


def get_document_repository(request: Request) -> SourceDocumentRepository:
    repository = getattr(request.app.state, "source_document_repository", None)
    if repository is None:
        repository = MongoSourceDocumentRepository(get_settings())
        request.app.state.source_document_repository = repository
    return repository


def _validate_official_source(domain: ExpertName, source_url: str) -> str:
    try:
        normalized_url = str(_SOURCE_URL.validate_python(source_url))
        parsed = urlsplit(normalized_url)
        port = parsed.port
    except (ValidationError, ValueError):
        raise HTTPException(status_code=422, detail="source_url must be a valid HTTPS URL.") from None
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        raise HTTPException(
            status_code=422,
            detail="source_url must be an HTTPS URL from the matching official Indian authority.",
        )
    allowed = OFFICIAL_SOURCE_HOSTS[domain]
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in allowed):
        authority = {"tax": "Income Tax Department", "investment": "SEBI", "insurance": "IRDAI"}[domain]
        raise HTTPException(
            status_code=422,
            detail=f"The {domain} document source must be hosted by the official {authority} domain.",
        )
    return normalized_url


def _validate_filename(filename: str | None) -> str:
    cleaned = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if not cleaned or len(cleaned) > 255:
        raise HTTPException(status_code=422, detail="A valid document filename is required.")
    if any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
        raise HTTPException(status_code=422, detail="The document filename contains invalid control characters.")
    suffix = PurePosixPath(cleaned).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Only UTF-8 plain text .txt and .md documents are accepted.",
        )
    return cleaned


def _validate_document_content(content: bytes, content_type: str | None) -> str:
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="The document exceeds Nugen's 100 MB per-file limit.")
    media_type = (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Nugen's developer edition accepts plain-text documents only.",
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="The document must use UTF-8 text encoding.") from None
    if "\x00" in text:
        raise HTTPException(status_code=415, detail="The document must contain plain text.")
    word_count = len(text.split())
    if word_count < MIN_DOCUMENT_WORDS:
        raise HTTPException(
            status_code=422,
            detail=f"Nugen requires at least {MIN_DOCUMENT_WORDS} words per document; received {word_count}.",
        )
    return text


async def _list_all_base_models(service: NugenAlignmentService):
    models = []
    offset = 0
    limit = 100
    while True:
        page = await service.base_models(limit=limit, offset=offset)
        models.extend(page.models)
        if len(page.models) < limit:
            return models
        offset += limit


@router.get("/nugen/base-models", response_model=BaseModelsResponse)
async def list_supported_base_models(
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: NugenAlignmentService = Depends(get_alignment_service),
) -> BaseModelsResponse:
    """List models Nugen currently marks as available for alignment."""

    response = await service.base_models(alignment_ready=True, limit=limit, offset=offset)
    return BaseModelsResponse(
        models=[
            model
            for model in response.models
            if model.type == "text-generation"
            and "alignment_ready" in model.model_fields_set
            and model.alignment_ready is True
            and "available_on_request" in model.model_fields_set
            and model.available_on_request is False
        ]
    )


@router.post("/documents", response_model=SourceDocumentRecord)
async def upload_source_document(
    domain: ExpertName = Form(...),
    file: UploadFile = File(...),
    source_url: str = Form(...),
    retrieved_on: date | None = Form(default=None),
    document_name: str | None = Form(default=None),
    service: NugenAlignmentService = Depends(get_alignment_service),
    repository: SourceDocumentRepository = Depends(get_document_repository),
    content_store: LocalSourceContentStore = Depends(get_source_content_store),
) -> SourceDocumentRecord:
    """Upload one official text source and keep its expert-domain provenance."""

    source_url = _validate_official_source(domain, source_url)
    filename = _validate_filename(file.filename)
    content = await file.read(MAX_DOCUMENT_BYTES + 1)
    _validate_document_content(content, file.content_type)
    display_name = (document_name or filename).strip()
    if not display_name or len(display_name) > 200:
        raise HTTPException(status_code=422, detail="document_name must be 1 to 200 characters.")

    await repository.check_ready()
    uploaded = await service.upload_documents(
        [NugenUploadFile(filename=filename, content=content, content_type="text/plain")],
        categories=[DOMAIN_CATEGORIES[domain]],
        names=[display_name],
    )
    if len(uploaded.document_ids) != 1 or not uploaded.document_ids[0].strip():
        raise NugenProtocolError(
            "Nugen did not return exactly one document ID for the one uploaded file."
        )

    record = SourceDocumentRecord(
        document_id=uploaded.document_ids[0],
        domain=domain,
        document_name=display_name,
        original_filename=filename,
        source_url=source_url,
        retrieved_on=retrieved_on or datetime.now(timezone.utc).date(),
        uploaded_at=datetime.now(timezone.utc),
        size_bytes=len(content),
    )
    try:
        await repository.save(record)
    except SourceDocumentStoreError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Nugen accepted document ID {record.document_id}, but FinPilot could not "
                "confirm its provenance record was saved. Check GET /api/nugen/documents and "
                "GET /api/documents before retrying; do not upload it again blindly."
            ),
        ) from None
    try:
        await content_store.save(domain, record.document_id, content)
    except SourceContentStoreError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Nugen document {record.document_id} was uploaded and registered, but its text "
                "could not be saved for retrieval. Check the registered document list; do not "
                "upload the document to Nugen again."
            ),
        ) from None
    return record


@router.get("/documents", response_model=SourceDocumentListResponse)
async def list_source_documents(
    domain: ExpertName = Query(...),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    repository: SourceDocumentRepository = Depends(get_document_repository),
) -> SourceDocumentListResponse:
    """List the locally registered source documents for one expert set."""

    documents = await repository.list(domain, limit=limit, offset=offset)
    return SourceDocumentListResponse(
        domain=domain,
        documents=documents,
        limit=limit,
        offset=offset,
    )


@router.get("/nugen/documents", response_model=SourceDocumentListResponse)
async def list_nugen_source_documents(
    domain: ExpertName = Query(...),
    status: DocumentStatusValue | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: NugenAlignmentService = Depends(get_alignment_service),
    repository: SourceDocumentRepository = Depends(get_document_repository),
) -> SourceDocumentListResponse:
    """List this domain's registered documents from Nugen's documented list API."""

    response = await service.list_documents(status=status, limit=limit, offset=offset)
    documents: list[SourceDocumentRecord] = []
    checked_at = datetime.now(timezone.utc)
    for provider_document in response.documents:
        if DOMAIN_CATEGORIES[domain] not in provider_document.categories:
            continue
        record = await repository.get(domain, provider_document.document_id)
        if record is None:
            continue
        await repository.update_status(
            domain,
            record.document_id,
            provider_document.status,
            checked_at.isoformat(),
        )
        documents.append(
            record.model_copy(
                update={
                    "nugen_status": provider_document.status,
                    "status_checked_at": checked_at,
                }
            )
        )
    return SourceDocumentListResponse(
        domain=domain,
        documents=documents,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/documents/{domain}/{document_id}/status",
    response_model=DocumentStatusCheckResponse,
)
async def check_source_document_status(
    domain: ExpertName,
    document_id: str,
    service: NugenAlignmentService = Depends(get_alignment_service),
    repository: SourceDocumentRepository = Depends(get_document_repository),
) -> DocumentStatusCheckResponse:
    record = await repository.get(domain, document_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Document is not registered in this expert set.")

    provider_status = await service.document_status(document_id)
    if provider_status.document_id not in (None, document_id):
        raise NugenProtocolError("Nugen returned a status for a different document ID.")
    checked_at = datetime.now(timezone.utc)
    await repository.update_status(
        domain,
        document_id,
        provider_status.status,
        checked_at.isoformat(),
    )
    record = record.model_copy(
        update={"nugen_status": provider_status.status, "status_checked_at": checked_at}
    )
    return DocumentStatusCheckResponse(
        document=record,
        provider_status=provider_status.status,
    )


async def _validate_active_source_set(
    domain: ExpertName,
    document_ids: list[str],
    service: NugenAlignmentService,
    repository: SourceDocumentRepository,
    content_store: LocalSourceContentStore,
) -> None:
    """Require every selected record, provider status, and local text to match."""

    checked_at = datetime.now(timezone.utc)
    for document_id in document_ids:
        record = await repository.get(domain, document_id)
        if record is None or record.domain != domain:
            raise HTTPException(
                status_code=404,
                detail=f"Document {document_id!r} is not registered in the {domain} set.",
            )

        status = await service.document_status(document_id)
        if status.document_id != document_id:
            raise NugenProtocolError(
                "Nugen did not confirm the selected document ID in its status response."
            )
        await repository.update_status(
            domain,
            document_id,
            status.status,
            checked_at.isoformat(),
        )
        if status.status != "READY":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Document {document_id!r} is {status.status}, not READY; "
                    "the active source selection was not changed."
                ),
            )
        try:
            text = await load_registered_document_text(domain, record, content_store)
        except SourceContentUnavailableError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Document {document_id!r} cannot be activated: {exc} "
                    "The active source selection was not changed."
                ),
            ) from None
        _, quality_issues = inspect_corpus_text(
            record.original_filename,
            text,
            domain=domain,
        )
        if quality_issues:
            issue_summary = "; ".join(quality_issues)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Document {document_id!r} cannot be activated because its local text "
                    f"failed corpus quality checks: {issue_summary}. "
                    "Review the document and activate a verified source; the active source selection was not changed."
                ),
            )


@router.get(
    "/documents/{domain}/active-source",
    response_model=ActiveSourceSelection,
)
async def get_active_source(
    domain: ExpertName,
    repository: SourceDocumentRepository = Depends(get_document_repository),
) -> ActiveSourceSelection:
    """Return the explicitly selected source set for one expert domain."""

    selection = await repository.get_active_source_selection(domain)
    if selection is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active source set has been explicitly selected for {domain}.",
        )
    return selection


@router.get("/source-health", response_model=SourceHealthResponse)
async def get_source_health(
    repository: SourceDocumentRepository = Depends(get_document_repository),
    content_store: LocalSourceContentStore = Depends(get_source_content_store),
) -> SourceHealthResponse:
    """Report active-source availability and local corpus quality without provider calls."""

    diagnostics = await diagnose_active_sources(repository, content_store)
    domains: list[SourceDomainHealth] = []
    for item in diagnostics:
        domain = item["domain"]
        status = item["status"]
        if status == "missing":
            message = (
                f"No active {domain} source is selected. Register a verified official corpus "
                "and activate it before asking this expert."
            )
        elif status == "invalid":
            message = (
                f"The active {domain} source fails local quality checks. Review the corpus, "
                "then register and activate a verified source before retrying."
            )
        else:
            message = f"The active {domain} source passed local quality checks."
        domains.append(
            SourceDomainHealth(
                domain=domain,
                active_document_ids=item["active_document_ids"],
                status=status,
                message=message,
                issues=item["issues"],
            )
        )
    return SourceHealthResponse(domains=domains)


@router.put(
    "/documents/{domain}/active-source",
    response_model=ActiveSourceSelection,
)
async def activate_source_set(
    domain: ExpertName,
    request: ActiveSourceUpdateRequest,
    service: NugenAlignmentService = Depends(get_alignment_service),
    repository: SourceDocumentRepository = Depends(get_document_repository),
    content_store: LocalSourceContentStore = Depends(get_source_content_store),
) -> ActiveSourceSelection:
    """Atomically activate an exact READY document set after local validation."""

    current = await repository.get_active_source_selection(domain)
    await _validate_active_source_set(
        domain,
        request.document_ids,
        service,
        repository,
        content_store,
    )
    try:
        return await repository.activate_source_selection(
            domain,
            request.document_ids,
            expected_revision=current.revision if current is not None else None,
            changed_at=datetime.now(timezone.utc),
        )
    except ActiveSourceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post(
    "/documents/{domain}/active-source/rollback",
    response_model=ActiveSourceSelection,
)
async def rollback_active_source(
    domain: ExpertName,
    service: NugenAlignmentService = Depends(get_alignment_service),
    repository: SourceDocumentRepository = Depends(get_document_repository),
    content_store: LocalSourceContentStore = Depends(get_source_content_store),
) -> ActiveSourceSelection:
    """Restore the last active source set after the same READY/content checks."""

    current = await repository.get_active_source_selection(domain)
    if current is None or not current.previous_document_ids:
        raise HTTPException(
            status_code=409,
            detail=f"No previous active source set is available for {domain} rollback.",
        )

    await _validate_active_source_set(
        domain,
        current.previous_document_ids,
        service,
        repository,
        content_store,
    )
    try:
        return await repository.activate_source_selection(
            domain,
            current.previous_document_ids,
            expected_revision=current.revision,
            changed_at=datetime.now(timezone.utc),
        )
    except ActiveSourceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


async def _validated_alignment_request(
    request: AlignmentPreviewRequest,
    service: NugenAlignmentService,
    repository: SourceDocumentRepository,
) -> tuple[CreateAlignmentRequest, list[SourceDocumentRecord]]:
    """Validate registered READY documents and an explicitly eligible base model."""
    records: list[SourceDocumentRecord] = []
    for document_id in request.document_ids:
        record = await repository.get(request.domain, document_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail=f"Document {document_id!r} is not registered in the {request.domain} set.",
            )
        records.append(record)

    ready_documents: list[SourceDocumentRecord] = []
    not_ready: list[tuple[str, str]] = []
    for record in records:
        status_response = await service.document_status(record.document_id)
        if status_response.document_id not in (None, record.document_id):
            raise NugenProtocolError("Nugen returned a status for a different document ID.")
        checked_at = datetime.now(timezone.utc)
        await repository.update_status(
            request.domain,
            record.document_id,
            status_response.status,
            checked_at.isoformat(),
        )
        updated = record.model_copy(
            update={
                "nugen_status": status_response.status,
                "status_checked_at": checked_at,
            }
        )
        if status_response.status == "READY":
            ready_documents.append(updated)
        else:
            not_ready.append((record.document_id, status_response.status))
    if not_ready:
        ids = ", ".join(f"{doc_id} ({status})" for doc_id, status in not_ready)
        raise HTTPException(
            status_code=409,
            detail=f"All selected documents must be READY before alignment review: {ids}.",
        )

    base_models = await _list_all_base_models(service)
    selected_model = next(
        (model for model in base_models if model.model_id == request.base_model_id),
        None,
    )
    if selected_model is None:
        raise HTTPException(
            status_code=422,
            detail="base_model_id was not returned by Nugen's base-model list.",
        )
    if selected_model.type != "text-generation":
        raise HTTPException(
            status_code=422,
            detail="The selected base model is not a text-generation model.",
        )
    if (
        "alignment_ready" not in selected_model.model_fields_set
        or selected_model.alignment_ready is not True
    ):
        raise HTTPException(
            status_code=422,
            detail="Nugen did not explicitly mark the selected model alignment_ready.",
        )
    if (
        "available_on_request" not in selected_model.model_fields_set
        or selected_model.available_on_request is not False
    ):
        raise HTTPException(
            status_code=409,
            detail="The selected base model is not confirmed as available without an access request.",
        )

    payload = CreateAlignmentRequest(
        alignment_name=request.alignment_name,
        base_model_id=selected_model.model_id,
        document_ids=request.document_ids,
        description=request.description,
    )
    return payload, ready_documents


@router.post("/alignments/prepare", response_model=AlignmentPreviewResponse)
async def prepare_alignment_request(
    request: AlignmentPreviewRequest,
    service: NugenAlignmentService = Depends(get_alignment_service),
    repository: SourceDocumentRepository = Depends(get_document_repository),
) -> AlignmentPreviewResponse:
    """Prepare and verify an alignment payload without submitting it."""

    payload, ready_documents = await _validated_alignment_request(
        request,
        service,
        repository,
    )
    return AlignmentPreviewResponse(
        domain=request.domain,
        request_payload=payload.model_dump(mode="json", exclude_none=True),
        documents=ready_documents,
    )


@router.post(
    "/alignments/submit",
    response_model=AlignmentJobRecord,
    status_code=202,
)
async def submit_alignment_request(
    request: AlignmentSubmitRequest,
    service: NugenAlignmentService = Depends(get_alignment_service),
    document_repository: SourceDocumentRepository = Depends(get_document_repository),
    job_repository: AlignmentJobRepository = Depends(get_alignment_job_repository),
) -> AlignmentJobRecord:
    """Explicitly submit one potentially costly provider alignment job."""

    preview_request = AlignmentPreviewRequest.model_validate(
        request.model_dump(exclude={"confirm_submission"})
    )
    payload, _ = await _validated_alignment_request(
        preview_request,
        service,
        document_repository,
    )
    now = datetime.now(timezone.utc)
    record = AlignmentJobRecord(
        tracking_id=uuid4().hex,
        domain=request.domain,
        alignment_name=payload.alignment_name,
        base_model_id=payload.base_model_id,
        document_ids=payload.document_ids,
        description=payload.description,
        submission_state="SUBMITTING",
        alignment_status="unaligned",
        verification_state="UNVERIFIED",
        inference_ready=False,
        status_note="Submission is in progress; no provider job ID is confirmed yet.",
        created_at=now,
        updated_at=now,
    )
    await job_repository.save(record)

    try:
        created = await service.create_alignment(payload)
    except NugenAPIError as exc:
        known_rejection = (
            exc.status_code is not None
            and 400 <= exc.status_code < 500
            and not isinstance(exc, (NugenTimeoutError, NugenTransportError))
        )
        state = "SUBMISSION_FAILED" if known_rejection else "SUBMISSION_UNKNOWN"
        record = record.model_copy(
            update={
                "submission_state": state,
                "verification_state": "FAILED" if known_rejection else "UNVERIFIED",
                "error_detail": str(exc),
                "status_note": (
                    "Nugen rejected the submission. No alignment job is confirmed."
                    if known_rejection
                    else "Submission outcome is unknown. Check Nugen before retrying to avoid a duplicate job."
                ),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await job_repository.save(record)
        raise HTTPException(
            status_code=502,
            detail={
                "tracking_id": record.tracking_id,
                "submission_state": record.submission_state,
                "message": record.status_note,
                "error_detail": record.error_detail,
            },
        ) from None
    except NugenConfigurationError as exc:
        record = record.model_copy(
            update={
                "submission_state": "SUBMISSION_FAILED",
                "verification_state": "FAILED",
                "error_detail": str(exc),
                "status_note": "Nugen is not configured; no alignment request was sent.",
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await job_repository.save(record)
        raise HTTPException(
            status_code=503,
            detail={
                "tracking_id": record.tracking_id,
                "submission_state": record.submission_state,
                "message": record.status_note,
            },
        ) from None
    except Exception:
        # A malformed success response or unexpected transport failure can
        # happen after Nugen accepted the job. Do not encourage an unsafe retry.
        record = record.model_copy(
            update={
                "submission_state": "SUBMISSION_UNKNOWN",
                "verification_state": "UNVERIFIED",
                "error_detail": "Nugen submission could not be confirmed from its response.",
                "status_note": "Submission outcome is unknown. Check Nugen before retrying to avoid a duplicate job.",
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await job_repository.save(record)
        raise HTTPException(
            status_code=502,
            detail={
                "tracking_id": record.tracking_id,
                "submission_state": record.submission_state,
                "message": record.status_note,
            },
        ) from None

    record = record.model_copy(
        update={
            "alignment_id": created.alignment_id,
            "submission_state": "SUBMITTED",
            "provider_status": created.status,
            "status_note": "Nugen accepted the alignment job; completion and inference availability are not yet verified.",
            "updated_at": datetime.now(timezone.utc),
        }
    )
    await job_repository.save(record)
    return record


@router.get("/alignment-jobs/{tracking_id}", response_model=AlignmentJobRecord)
async def get_alignment_submission_record(
    tracking_id: str,
    job_repository: AlignmentJobRepository = Depends(get_alignment_job_repository),
) -> AlignmentJobRecord:
    record = await job_repository.get_by_tracking_id(tracking_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Alignment tracking record was not found.")
    return record


async def _refresh_or_record_error(
    record: AlignmentJobRecord,
    service: NugenAlignmentService,
    job_repository: AlignmentJobRepository,
) -> AlignmentJobRecord:
    return await refresh_alignment_job(record, service, job_repository)


@router.get(
    "/alignments/{alignment_id}/status",
    response_model=AlignmentJobRecord,
)
async def refresh_alignment_status(
    alignment_id: str,
    service: NugenAlignmentService = Depends(get_alignment_service),
    job_repository: AlignmentJobRepository = Depends(get_alignment_job_repository),
) -> AlignmentJobRecord:
    record = await job_repository.get_by_alignment_id(alignment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Alignment ID is not tracked by FinPilot.")
    return await _refresh_or_record_error(record, service, job_repository)


@router.post(
    "/alignments/{alignment_id}/deploy",
    response_model=AlignmentJobRecord,
)
async def explicitly_deploy_alignment(
    alignment_id: str,
    request: AlignmentDeploymentRequest,
    service: NugenAlignmentService = Depends(get_alignment_service),
    job_repository: AlignmentJobRepository = Depends(get_alignment_job_repository),
) -> AlignmentJobRecord:
    """Deploy only after an explicit confirmation and provider-verified completion."""

    record = await job_repository.get_by_alignment_id(alignment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Alignment ID is not tracked by FinPilot.")
    record = await _refresh_or_record_error(record, service, job_repository)
    if record.inference_ready:
        return record
    if record.provider_status not in {"READY", "EVALUATED", "UNDEPLOYED"} or not record.model_id:
        raise HTTPException(
            status_code=409,
            detail="Nugen has not confirmed a completed alignment and resulting model ID.",
        )
    if record.deployment_status == "DEPLOYING":
        return record
    if record.deployment_status != "UNDEPLOYED":
        raise HTTPException(
            status_code=409,
            detail="Nugen has not confirmed this model is in a deployable UNDEPLOYED state.",
        )

    try:
        returned_model_id = await service.deploy_model(record.model_id)
        if returned_model_id != record.model_id:
            raise NugenProtocolError("Nugen returned a different model ID after deployment request.")
    except (NugenAPIError, NugenConfigurationError) as exc:
        updated = record.model_copy(
            update={
                "alignment_status": "unaligned",
                "verification_state": "UNVERIFIED",
                "inference_ready": False,
                "error_detail": str(exc),
                "status_note": "Deployment request failed or is uncertain; poll Nugen before retrying.",
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await job_repository.save(updated)
        return updated

    return await _refresh_or_record_error(record, service, job_repository)
