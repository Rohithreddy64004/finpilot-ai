"""Feedback and explicit reviewer-verified correction endpoints."""

from __future__ import annotations

import hmac
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException

from backend.app.config import Settings, get_settings
from backend.app.interactions import InteractionRepository, InteractionStoreError, get_interaction_repository
from backend.app.schemas import (
    FeedbackCreatedResponse,
    FeedbackRecord,
    FeedbackRequest,
    FeedbackSummaryResponse,
    VerifiedCorrectionRecord,
    VerifiedCorrectionRequest,
)


router = APIRouter(prefix="/api", tags=["feedback and review"])


@router.post("/feedback", response_model=FeedbackCreatedResponse, status_code=201)
async def submit_feedback(
    request: FeedbackRequest,
    repository: InteractionRepository = Depends(get_interaction_repository),
) -> FeedbackCreatedResponse:
    """Store user opinion separately; it never changes verified corrections."""

    try:
        query = await repository.get_query(request.query_id)
        if query is None:
            raise HTTPException(status_code=404, detail="The referenced query was not found.")
        record = FeedbackRecord(
            id=uuid4().hex,
            query_id=request.query_id,
            rating=request.rating,
            comment=request.comment,
            correct_experts=request.correct_experts,
            timestamp=datetime.now(timezone.utc),
        )
        await repository.save_feedback(record)
    except InteractionStoreError:
        raise HTTPException(status_code=503, detail="Feedback could not be saved safely.") from None
    return FeedbackCreatedResponse(id=record.id, query_id=record.query_id)


@router.get("/feedback/summary", response_model=FeedbackSummaryResponse)
async def get_feedback_summary(
    repository: InteractionRepository = Depends(get_interaction_repository),
) -> FeedbackSummaryResponse:
    try:
        return await repository.feedback_summary()
    except InteractionStoreError:
        raise HTTPException(status_code=503, detail="Feedback summary is unavailable.") from None


@router.post("/verified-corrections", response_model=VerifiedCorrectionRecord, status_code=201)
async def submit_verified_correction(
    request: VerifiedCorrectionRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    repository: InteractionRepository = Depends(get_interaction_repository),
) -> VerifiedCorrectionRecord:
    """Save a correction only after explicit reviewer authentication and attestation."""

    configured = settings.correction_review_token
    expected_token = configured.get_secret_value() if configured is not None else ""
    supplied_token = ""
    if authorization is not None:
        scheme, separator, token = authorization.partition(" ")
        if separator and scheme.casefold() == "bearer":
            supplied_token = token.strip()
    if not expected_token or not supplied_token or not hmac.compare_digest(
        supplied_token, expected_token
    ):
        raise HTTPException(status_code=403, detail="Reviewer authorization is required.")

    try:
        if await repository.get_query(request.query_id) is None:
            raise HTTPException(status_code=404, detail="The referenced query was not found.")
        record = VerifiedCorrectionRecord(
            id=uuid4().hex,
            query_id=request.query_id,
            correction=request.correction,
            verified_by=request.verified_by,
            verification_note=request.verification_note,
            source_references=request.source_references,
            verified_at=datetime.now(timezone.utc),
        )
        await repository.save_verified_correction(record)
    except InteractionStoreError:
        raise HTTPException(status_code=503, detail="The reviewed correction could not be saved.") from None
    return record
