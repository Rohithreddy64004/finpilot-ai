"""Typed request and response models for documented Nugen API v3 endpoints."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NugenSchema(BaseModel):
    """Provider response base; keep forward-compatible undocumented fields."""

    model_config = ConfigDict(extra="allow")


class InferenceSettingsResponse(NugenSchema):
    enabled: bool
    models: dict[str, bool] = Field(default_factory=dict)


class BaseModelSummary(NugenSchema):
    model_id: str
    model_name: str
    parameters: str = "N/A"
    alignment_ready: bool = False
    inference_ready: bool = False
    available_on_request: bool = False
    type: str
    model_card: str | None = None


class BaseModelsResponse(NugenSchema):
    models: list[BaseModelSummary]


class NugenMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant"]
    content: str | list[dict[str, Any]]
    name: str | None = Field(default=None, max_length=64, pattern=r"^[a-zA-Z0-9_]+$")


class ReasoningFields(BaseModel):
    model_config = ConfigDict(extra="allow")

    effort: Literal["xhigh", "high", "medium", "low", "minimal", "none"] | None = None
    max_tokens: int | None = None
    exclude: bool = False
    enabled: bool | None = None


class ChatCompletionRequest(BaseModel):
    """Non-streaming subset of the documented chat completion request."""

    model_config = ConfigDict(extra="forbid")

    model: str
    messages: list[NugenMessage] = Field(min_length=1)
    max_tokens: int = 200
    prompt_truncate_len: int = 1500
    temperature: float = Field(default=0.75, ge=0, le=2)
    stream: Literal[False] = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    top_p: float | None = None
    top_k: int | None = None
    n: int = 1
    reasoning: ReasoningFields | None = None
    stream_options: dict[str, Any] | None = None


class CompletionMessage(NugenSchema):
    role: Literal["assistant"]
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class CompletionChoice(NugenSchema):
    index: int
    message: CompletionMessage
    finish_reason: str | None = None


class CompletionUsage(NugenSchema):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(NugenSchema):
    id: str
    object: Literal["chat.completion"]
    created: float
    model: str
    choices: list[CompletionChoice]
    usage: CompletionUsage | None = None
    confidence_score: float | None = None


DocumentStatusValue = Literal["PROCESSING", "READY", "FAILED"]
ResourceStatusValue = Literal[
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
DeploymentStatusValue = Literal["UNDEPLOYED", "DEPLOYING", "DEPLOYED", "UNDEPLOYING"]


class UploadedDocumentsResponse(NugenSchema):
    document_ids: list[str]


class DocumentStatusResponse(NugenSchema):
    status: DocumentStatusValue
    document_id: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None
    progress: float | None = None
    eta_seconds: int | None = None


class DocumentSummary(NugenSchema):
    document_id: str
    document_name: str | None = None
    status: DocumentStatusValue
    created_at: datetime
    categories: list[str]


class DocumentCategory(NugenSchema):
    name: str
    count: int = Field(ge=0)


class DocumentsResponse(NugenSchema):
    documents: list[DocumentSummary]
    categories: list[DocumentCategory]


class CreateAlignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alignment_name: str
    base_model_id: str
    document_ids: list[str]
    workflow_id: str | None = None
    benchmark_id: str | None = None
    description: str | None = None


class CreateAlignmentResponse(NugenSchema):
    alignment_id: str = Field(min_length=1)
    status: Literal["PROCESSING"] = "PROCESSING"
    message: str | None = None


class AlignmentStatusResponse(NugenSchema):
    alignment_id: str
    status: ResourceStatusValue
    created_at: str | None = None
    completed_at: str | None = None
    updated_at: str | None = None
    progress: float | None = None
    eta_seconds: int | None = None
    queue_position: int | None = None
    stop_requested_at: str | None = None
    early_deployable: bool = False
    evaluation_id: str | None = None
    evaluation_status: ResourceStatusValue | None = None
    evaluation_message: str | None = None


class AlignedModelSummary(NugenSchema):
    model_id: str
    model_name: str
    deployment_status: DeploymentStatusValue | None
    base_model_id: str
    alignment_id: str | None = None
    created_at: str


class AlignedModelsResponse(NugenSchema):
    domain_aligned_models: list[AlignedModelSummary]


class DeployAlignedModelResponse(NugenSchema):
    model_id: str


class DeploymentStatusResponse(NugenSchema):
    model_id: str
    status: DeploymentStatusValue
    error: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None
    progress: float | None = None
    eta_seconds: int | None = None
