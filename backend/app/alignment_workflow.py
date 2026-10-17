"""Provider-confirmed alignment and inference-readiness checks."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.app.alignment_jobs import AlignmentJobRepository
from backend.app.nugen.alignment_service import NugenAlignmentService
from backend.app.nugen.errors import (
    NugenAPIError,
    NugenConfigurationError,
    NugenNotFoundError,
    NugenProtocolError,
)
from backend.app.nugen.schemas import AlignedModelSummary
from backend.app.schemas import AlignmentJobRecord


ALIGNMENT_COMPLETED_STATES = {"READY", "EVALUATED", "UNDEPLOYED"}


async def _models_for_alignment(
    service: NugenAlignmentService,
    alignment_id: str,
) -> list[AlignedModelSummary]:
    matches: list[AlignedModelSummary] = []
    offset = 0
    while True:
        try:
            page = await service.aligned_models(limit=100, offset=offset)
        except NugenNotFoundError:
            return matches
        matches.extend(
            model
            for model in page.domain_aligned_models
            if model.alignment_id == alignment_id
        )
        if len(page.domain_aligned_models) < 100:
            return matches
        offset += 100


async def refresh_alignment_job(
    record: AlignmentJobRecord,
    service: NugenAlignmentService,
    repository: AlignmentJobRepository,
) -> AlignmentJobRecord:
    """Refresh one tracked job and require a matching deployed model to verify it."""

    if not record.alignment_id:
        return record

    try:
        return await _refresh_alignment_job(record, service, repository)
    except (NugenAPIError, NugenConfigurationError) as exc:
        latest = await repository.get_by_tracking_id(record.tracking_id) or record
        updated = latest.model_copy(
            update={
                # A failed refresh invalidates any previously observed model
                # or deployment state. In particular, never let a stale
                # UNDEPLOYED status authorize a deployment call.
                "provider_status": None,
                "model_id": None,
                "listed_deployment_status": None,
                "deployment_status": None,
                "alignment_status": "unaligned",
                "verification_state": "UNVERIFIED",
                "inference_ready": False,
                "error_detail": str(exc),
                "status_note": "The latest provider check failed; model availability is unverified.",
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await repository.save(updated)
        return updated


async def _refresh_alignment_job(
    record: AlignmentJobRecord,
    service: NugenAlignmentService,
    repository: AlignmentJobRepository,
) -> AlignmentJobRecord:
    """Perform the provider calls after the public safe-error wrapper."""

    now = datetime.now(timezone.utc)
    job_status = await service.alignment_status(record.alignment_id)
    if job_status.alignment_id != record.alignment_id:
        raise NugenProtocolError("Nugen returned a status for a different alignment ID.")

    updated = record.model_copy(
        update={
            "submission_state": "SUBMITTED",
            "provider_status": job_status.status,
            "model_id": None,
            "listed_deployment_status": None,
            "deployment_status": None,
            "alignment_status": "unaligned",
            "verification_state": "UNVERIFIED",
            "inference_ready": False,
            "error_detail": None,
            "status_note": (
                f"Nugen reports {job_status.status}; the resulting model has not yet been "
                "confirmed as available for inference."
            ),
            "updated_at": now,
            "status_checked_at": now,
        }
    )
    await repository.save(updated)

    if job_status.status in {"FAILED", "STOPPED"}:
        updated = updated.model_copy(
            update={
                "verification_state": "FAILED",
                "error_detail": (
                    f"Nugen reports alignment status {job_status.status}; the documented "
                    "status response does not include the alignment failure reason."
                ),
                "status_note": (
                    f"Nugen reports {job_status.status}. Failure detail is not available "
                    "in the documented status response."
                ),
            }
        )
        await repository.save(updated)
        return updated

    if job_status.status not in ALIGNMENT_COMPLETED_STATES:
        updated = updated.model_copy(
            update={
                "verification_state": "PENDING",
                "status_note": f"Nugen alignment job is {job_status.status}; no aligned inference model is confirmed.",
            }
        )
        await repository.save(updated)
        return updated

    models = await _models_for_alignment(service, record.alignment_id)
    if len(models) != 1:
        note = (
            "No aligned model row is linked to this alignment ID yet."
            if not models
            else "Multiple aligned model rows match this alignment ID; model identity is ambiguous."
        )
        updated = updated.model_copy(
            update={
                "verification_state": "UNVERIFIED",
                "status_note": note,
            }
        )
        await repository.save(updated)
        return updated

    aligned_model = models[0]
    if aligned_model.base_model_id != record.base_model_id:
        updated = updated.model_copy(
            update={
                "model_id": aligned_model.model_id,
                "listed_deployment_status": aligned_model.deployment_status,
                "verification_state": "UNVERIFIED",
                "status_note": "The aligned model row does not match the tracked base model ID.",
            }
        )
        await repository.save(updated)
        return updated

    deployment = await service.deployment_status(aligned_model.model_id)
    if deployment.model_id != aligned_model.model_id:
        raise NugenProtocolError("Nugen returned deployment status for a different model ID.")

    same_deployment_state = deployment.status == aligned_model.deployment_status
    inference_ready = (
        job_status.status in ALIGNMENT_COMPLETED_STATES
        and aligned_model.deployment_status == "DEPLOYED"
        and deployment.status == "DEPLOYED"
        and same_deployment_state
    )
    if inference_ready:
        verification_state = "VERIFIED"
        status_note = (
            "Nugen confirms alignment completion, the aligned model's alignment ID, and DEPLOYED status."
        )
    elif deployment.status == "DEPLOYING" or aligned_model.deployment_status == "DEPLOYING":
        verification_state = "PENDING"
        status_note = "Alignment completed; Nugen deployment is still in progress."
    elif deployment.status == "DEPLOYED" and not same_deployment_state:
        verification_state = "UNVERIFIED"
        status_note = "Nugen's aligned-model list and deployment-status response disagree."
    else:
        verification_state = "READY_NOT_DEPLOYED"
        status_note = "Alignment completed, but Nugen does not confirm the model as DEPLOYED for inference."

    updated = updated.model_copy(
        update={
            "model_id": aligned_model.model_id,
            "listed_deployment_status": aligned_model.deployment_status,
            "deployment_status": deployment.status,
            "alignment_status": "aligned" if inference_ready else "unaligned",
            "verification_state": verification_state,
            "inference_ready": inference_ready,
            "status_note": status_note,
        }
    )
    await repository.save(updated)
    return updated
