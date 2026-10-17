"""Typed API and orchestration schemas for FinPilot AI."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


ExpertName = Literal["tax", "investment", "insurance"]
AlignmentStatus = Literal["aligned", "unaligned"]
ClaimStatus = Literal["supported", "contradicted", "unverified"]
ClaimAssessmentBasis = Literal[
    "independent_verifier",
    "source_text_comparison",
    "unavailable",
]
ClaimVerificationSummaryStatus = Literal[
    "fully_verified",
    "partially_verified",
    "evidence_supported",
    "partially_supported",
    "verification_unavailable",
    "unverified",
    "contains_contradiction",
    "not_performed",
]
AlignmentSubmissionState = Literal[
    "SUBMITTING",
    "SUBMITTED",
    "SUBMISSION_UNKNOWN",
    "SUBMISSION_FAILED",
]
AlignmentProviderStatus = Literal[
    "PROCESSING",
    "READY",
    "FAILED",
    "DEPLOYING",
    "EVALUATING",
    "UNDEPLOYED",
    "EVALUATED",
    "STOPPED",
    "QUEUED",
]
AlignmentVerificationState = Literal[
    "UNVERIFIED",
    "PENDING",
    "FAILED",
    "READY_NOT_DEPLOYED",
    "VERIFIED",
]
AlignedDeploymentStatus = Literal["UNDEPLOYED", "DEPLOYING", "DEPLOYED", "UNDEPLOYING"]


class ChatRequest(BaseModel):
    """A user's finance education question."""

    question: str = Field(min_length=1, max_length=10_000)
    applicable_as_of: date | None = None

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class SourceReference(BaseModel):
    """A source that can be shown alongside an expert's conclusion."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    publisher: str | None = None
    url: AnyHttpUrl | None = None
    published_on: date | None = None
    accessed_on: date | None = None


class EvidenceItem(BaseModel):
    """A concise evidence statement with an optional source reference."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    source: SourceReference | None = None


class ExpertResult(BaseModel):
    """Structured result returned by a domain expert adapter."""

    model_config = ConfigDict(extra="forbid")

    expert: ExpertName
    alignment_status: AlignmentStatus = "unaligned"
    conclusion: str = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """Synthesized educational answer and its supporting material."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    expert_results: list[ExpertResult] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    applicable_as_of: date | None = None
    educational_disclaimer: str = (
        "FinPilot AI provides general financial education, not licensed advice."
    )


class DomainInferenceRequest(BaseModel):
    """One-shot question sent to a verified Nugen text-generation model."""

    model_config = ConfigDict(extra="forbid")

    domain: ExpertName
    question: str = Field(min_length=1, max_length=10_000)
    model_id: str | None = Field(default=None, min_length=1, max_length=300)
    applicable_as_of: date | None = None

    @field_validator("question")
    @classmethod
    def inference_question_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value

    @field_validator("model_id")
    @classmethod
    def inference_model_id_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("model_id must not be blank")
        return value


class RetrievedEvidence(BaseModel):
    """One excerpt sent to the model, linked to its registered source record."""

    model_config = ConfigDict(extra="forbid")

    citation_id: str = Field(pattern=r"^S[1-9][0-9]*$")
    passage_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    document_name: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    source: SourceReference


class ClaimVerification(BaseModel):
    """One candidate factual claim and an explicit, evidence-linked assessment."""

    model_config = ConfigDict(extra="forbid")

    claim_text: str = Field(min_length=1)
    status: ClaimStatus
    assessment_basis: ClaimAssessmentBasis = "unavailable"
    evidence_ids: list[str] = Field(default_factory=list)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    note: str = ""


class DomainInferenceResponse(BaseModel):
    """Answer metadata makes model alignment and grounding limits explicit."""

    model_config = ConfigDict(extra="forbid")

    domain: ExpertName
    model_id: str
    alignment_status: AlignmentStatus
    answer: str = Field(min_length=1)
    evidence: list[RetrievedEvidence] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)
    source_grounding_supported: bool = False
    source_grounding_note: str = "No relevant source passage was retrieved for this answer."
    claim_verifications: list[ClaimVerification] = Field(default_factory=list)
    claim_verification_status: ClaimVerificationSummaryStatus = "not_performed"
    independent_verification_performed: bool = False
    claim_verification_note: str = (
        "No independent claim verifier is configured; claim entailment is unverified."
    )
    limitations: list[str] = Field(default_factory=list)
    applicable_as_of: date
    educational_disclaimer: str = (
        "FinPilot AI provides general financial education, not licensed advice."
    )


class HealthResponse(BaseModel):
    """Basic process liveness response."""

    status: Literal["ok"] = "ok"
    service: str


class APIIndexResponse(BaseModel):
    """Small API root response that points operators to useful checks."""

    service: str
    docs_path: Literal["/docs"] = "/docs"
    health_path: Literal["/health"] = "/health"
    readiness_path: Literal["/ready"] = "/ready"


class ReadinessResponse(BaseModel):
    """Readiness after the persistence registry and Nugen credential are checked."""

    status: Literal["ready"] = "ready"
    service: str
    mongodb: Literal["ready"] = "ready"
    nugen_api_credential: Literal["configured"] = "configured"


class SourceDocumentRecord(BaseModel):
    """Locally retained provenance and last-known status for one Nugen document."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    domain: ExpertName
    document_name: str = Field(min_length=1, max_length=200)
    original_filename: str = Field(min_length=1, max_length=255)
    source_url: AnyHttpUrl
    retrieved_on: date
    uploaded_at: datetime
    size_bytes: int = Field(gt=0)
    nugen_status: Literal["PROCESSING", "READY", "FAILED"] | None = None
    status_checked_at: datetime | None = None


class SourceDocumentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: ExpertName
    documents: list[SourceDocumentRecord]
    limit: int
    offset: int


class ActiveSourceUpdateRequest(BaseModel):
    """An explicit replacement set for one domain's inference corpus."""

    model_config = ConfigDict(extra="forbid")

    document_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("document_ids")
    @classmethod
    def active_document_ids_must_be_unique_and_nonblank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("document_ids must not contain blank values")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("document_ids must be unique")
        return cleaned


class ActiveSourceSelection(BaseModel):
    """Persisted active and previous document sets for explicit rollback."""

    model_config = ConfigDict(extra="forbid")

    domain: ExpertName
    active_document_ids: list[str] = Field(min_length=1, max_length=100)
    previous_document_ids: list[str] = Field(default_factory=list, max_length=100)
    revision: int = Field(ge=1)
    changed_at: datetime

    @field_validator("active_document_ids", "previous_document_ids")
    @classmethod
    def source_document_ids_must_be_unique_and_nonblank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("active source document IDs must be unique and nonblank")
        return cleaned


class DocumentStatusCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: SourceDocumentRecord
    provider_status: Literal["PROCESSING", "READY", "FAILED"]


class AlignmentPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: ExpertName
    alignment_name: str = Field(min_length=1, max_length=120)
    base_model_id: str = Field(min_length=1, max_length=300)
    document_ids: list[str] = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("document_ids")
    @classmethod
    def document_ids_must_be_unique_and_nonblank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("document_ids must not contain blank values")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("document_ids must be unique")
        return cleaned


class AlignmentPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: ExpertName
    request_payload: dict[str, Any]
    documents: list[SourceDocumentRecord]
    submitted: Literal[False] = False
    notice: str = "Preview only; no alignment job has been submitted."


class AlignmentSubmitRequest(AlignmentPreviewRequest):
    """Explicitly confirmed submission; this request can start a paid job."""

    confirm_submission: Literal[True]


class AlignmentDeploymentRequest(BaseModel):
    """Explicit confirmation for the separately billable provider deployment."""

    model_config = ConfigDict(extra="forbid")

    confirm_deployment: Literal[True]


class AlignmentJobRecord(BaseModel):
    """Persistent local record connecting a domain, provider job, and model."""

    model_config = ConfigDict(extra="forbid")

    tracking_id: str = Field(min_length=1)
    domain: ExpertName
    alignment_name: str = Field(min_length=1, max_length=120)
    base_model_id: str = Field(min_length=1, max_length=300)
    document_ids: list[str] = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    alignment_id: str | None = Field(default=None, min_length=1)
    submission_state: AlignmentSubmissionState
    provider_status: AlignmentProviderStatus | None = None
    model_id: str | None = Field(default=None, min_length=1)
    listed_deployment_status: AlignedDeploymentStatus | None = None
    deployment_status: AlignedDeploymentStatus | None = None
    alignment_status: AlignmentStatus = "unaligned"
    verification_state: AlignmentVerificationState = "UNVERIFIED"
    inference_ready: bool = False
    error_detail: str | None = Field(default=None, max_length=2000)
    status_note: str = Field(min_length=1)
    status_checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
