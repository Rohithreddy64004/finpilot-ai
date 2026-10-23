"""Typed API and orchestration schemas for FinPilot AI."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


ExpertName = Literal["tax", "investment", "insurance"]
AlignmentStatus = Literal["aligned", "unaligned"]
ModelMode = Literal["aligned", "base_fallback", "base_fallback_after_validation_failure"]
AnswerState = Literal["validated", "no_passages", "withheld"]
FinalAnswerState = Literal["validated", "no_passages", "withheld", "out_of_scope"]
AnswerMode = Literal["generated", "extractive", "none"]
FinalAnswerMode = Literal["generated", "extractive", "mixed", "none"]
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

    question: str = Field(min_length=1, max_length=2_000)
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
    question: str = Field(min_length=1, max_length=2_000)
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
    section_heading: str = "Source passage"
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
    model_mode: ModelMode = "base_fallback"
    alignment_status: AlignmentStatus
    answer: str = Field(min_length=1)
    answer_state: AnswerState = "validated"
    answer_mode: AnswerMode = "none"
    repair_retry_used: bool = False
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


class AskRequest(BaseModel):
    """A multi-expert educational question with an optional expert override."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2_000)
    experts: list[ExpertName] | None = Field(default=None, min_length=1, max_length=3)
    applicable_date: date | None = None

    @field_validator("question")
    @classmethod
    def ask_question_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value

    @field_validator("experts")
    @classmethod
    def ask_experts_must_be_unique(
        cls,
        value: list[ExpertName] | None,
    ) -> list[ExpertName] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("experts must not contain duplicates")
        return value


class AskRoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experts: list[ExpertName] = Field(max_length=3)
    reason: str = Field(min_length=1, max_length=500)
    routing_method: Literal["model", "fallback", "override"]


class AskSource(BaseModel):
    """Globally unique citation identity for one passage in a multi-expert answer."""

    model_config = ConfigDict(extra="forbid")

    expert: ExpertName
    citation_id: str = Field(pattern=r"^S[1-9][0-9]*$")
    passage_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    document_name: str = Field(min_length=1)
    section_heading: str = "Source passage"
    excerpt: str = Field(min_length=1)
    source: SourceReference


class AskExpertResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expert: ExpertName
    status: Literal["complete", "error"]
    answer: str = Field(min_length=1)
    model_id: str | None = None
    model_mode: ModelMode | None = None
    alignment_status: AlignmentStatus = "unaligned"
    answer_state: AnswerState = "no_passages"
    answer_mode: AnswerMode = "none"
    repair_retry_used: bool = False
    source_grounding_supported: bool = False
    source_grounding_note: str = "No supporting passage was found."
    evidence: list[AskSource] = Field(default_factory=list)
    claim_verifications: list[ClaimVerification] = Field(default_factory=list)
    claim_verification_status: ClaimVerificationSummaryStatus = "not_performed"
    independent_verification_performed: bool = False
    claim_verification_note: str = "No claim verification has been performed."
    limitations: list[str] = Field(default_factory=list)
    error: str | None = None


class AskStageLatency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routing_ms: int = Field(ge=0)
    experts_ms: int = Field(ge=0)
    per_expert_ms: dict[ExpertName, int] = Field(default_factory=dict)
    synthesis_ms: int = Field(ge=0)
    total_ms: int = Field(ge=0)


class AskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    question: str
    routing: AskRoutingDecision
    experts: list[AskExpertResult]
    final_answer: str = Field(min_length=1)
    final_answer_state: FinalAnswerState = "validated"
    final_answer_mode: FinalAnswerMode = "none"
    final_excerpt_source_ids: list[str] = Field(default_factory=list)
    final_answer_source_grounding_supported: bool = False
    final_answer_source_grounding_note: str = "No source-grounded final answer was returned."
    final_answer_claim_verifications: list[ClaimVerification] = Field(default_factory=list)
    final_answer_claim_verification_status: ClaimVerificationSummaryStatus = "not_performed"
    final_answer_independent_verification_performed: bool = False
    final_answer_claim_verification_note: str = "No final claim verification has been performed."
    sources: list[AskSource] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    latency_ms: AskStageLatency


class QueryLogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2_000)
    routing: AskRoutingDecision
    timestamp: datetime


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(min_length=1, max_length=100)
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=2_000)
    correct_experts: list[ExpertName] | None = Field(default=None, max_length=3)

    @field_validator("query_id")
    @classmethod
    def feedback_query_id_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query_id must not be blank")
        return value

    @field_validator("comment")
    @classmethod
    def feedback_comment_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("correct_experts")
    @classmethod
    def corrected_experts_must_be_unique(
        cls,
        value: list[ExpertName] | None,
    ) -> list[ExpertName] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("correct_experts must not contain duplicates")
        return value


class FeedbackRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    rating: Literal["up", "down"]
    comment: str | None = None
    correct_experts: list[ExpertName] | None = None
    timestamp: datetime


class FeedbackCreatedResponse(BaseModel):
    id: str
    query_id: str
    notice: str = (
        "Feedback is stored separately and is not treated as a verified correction."
    )


class FeedbackSummaryResponse(BaseModel):
    total: int = Field(ge=0)
    thumbs_up: int = Field(ge=0)
    thumbs_down: int = Field(ge=0)
    correct_expert_counts: dict[ExpertName, int] = Field(default_factory=dict)
    verified_correction_count: int = Field(ge=0)


class VerifiedCorrectionRequest(BaseModel):
    """Reviewer-attested correction; never populated from ordinary feedback."""

    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(min_length=1, max_length=100)
    correction: str = Field(min_length=1, max_length=4_000)
    verified_by: str = Field(min_length=2, max_length=200)
    verification_note: str = Field(min_length=10, max_length=2_000)
    source_references: list[SourceReference] = Field(min_length=1, max_length=10)

    @field_validator("source_references")
    @classmethod
    def correction_sources_must_be_https(
        cls,
        value: list[SourceReference],
    ) -> list[SourceReference]:
        if any(source.url is None or source.url.scheme != "https" for source in value):
            raise ValueError("verified corrections require HTTPS source references")
        return value


class VerifiedCorrectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    correction: str = Field(min_length=1, max_length=4_000)
    verified_by: str = Field(min_length=2, max_length=200)
    verification_note: str = Field(min_length=10, max_length=2_000)
    source_references: list[SourceReference] = Field(min_length=1, max_length=10)
    verified_at: datetime


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


class SourceDomainHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: ExpertName
    active_document_ids: list[str] = Field(default_factory=list)
    status: Literal["ready", "missing", "invalid"]
    message: str = Field(min_length=1)
    issues: list[str] = Field(default_factory=list)


class SourceHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domains: list[SourceDomainHealth] = Field(min_length=3, max_length=3)


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
