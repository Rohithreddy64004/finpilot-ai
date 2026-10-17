"""Multi-expert ask orchestration using the existing domain inference pipeline."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from backend.app.alignment_jobs import AlignmentJobRepository, get_alignment_job_repository
from backend.app.claim_verification import (
    ClaimVerifier,
    UnconfiguredClaimVerifier,
    verify_answer_claims,
)
from backend.app.config import Settings, get_settings
from backend.app.evidence_retrieval import RetrievedPassage, citation_reference_ids
from backend.app.inference_api import (
    get_claim_verifier,
    run_domain_inference,
)
from backend.app.interactions import (
    InteractionRepository,
    InteractionStoreError,
    get_interaction_repository,
)
from backend.app.nugen.claim_verifier import NugenClaimVerifier
from backend.app.nugen.client import NugenClient
from backend.app.nugen.errors import NugenAPIError, NugenConfigurationError
from backend.app.rate_limit import enforce_api_rate_limit
from backend.app.router import RouterDecision, route
from backend.app.schemas import (
    AskExpertResult,
    AskRequest,
    AskResponse,
    AskRoutingDecision,
    AskSource,
    AskStageLatency,
    ClaimVerification,
    DomainInferenceRequest,
    DomainInferenceResponse,
    ExpertName,
    QueryLogRecord,
)
from backend.app.source_content_store import LocalSourceContentStore, get_source_content_store
from backend.app.source_documents import SourceDocumentRepository, SourceDocumentStoreError
from backend.app.synthesizer import synthesize
from backend.app.workflow_api import get_document_repository


router = APIRouter(prefix="/api", tags=["multi-expert ask"])
_CITATION = re.compile(r"\[(S\d+)\]")
_CITATION_LIKE = re.compile(r"\[S[^\]\r\n]*\]")


async def get_ask_client() -> AsyncIterator[NugenClient]:
    """Create a lazy client so the router can fall back when credentials are absent."""

    client = NugenClient(get_settings())
    try:
        yield client
    finally:
        await client.aclose()


def _remap_citations(text: str, mapping: dict[str, str]) -> str:
    return _CITATION.sub(lambda match: f"[{mapping.get(match.group(1), 'S0')}]", text)


def _validate_citations(answer: str, sources: list[AskSource]) -> tuple[bool, str]:
    known = {source.citation_id for source in sources}
    cited = citation_reference_ids(answer)
    unknown = cited - known
    malformed = [
        marker
        for marker in _CITATION_LIKE.findall(answer)
        if re.fullmatch(r"\[S\d+\]", marker) is None
    ]
    if unknown:
        return False, "The answer cited source IDs that were not returned by any expert; wording was withheld."
    if malformed:
        return False, "The answer contained malformed source markers; wording was withheld."
    if not cited:
        return False, "The answer contained no source markers; synthesized wording was withheld."
    return True, "Every citation marker maps to a returned expert passage."


def _to_retrieved_passage(source: AskSource) -> RetrievedPassage:
    return RetrievedPassage(
        citation_id=source.citation_id,
        passage_id=source.passage_id,
        document_id=source.document_id,
        document_name=source.document_name,
        excerpt=source.excerpt,
        source=source.source,
        score=0.0,
    )


def _remap_claims(
    claims: list[ClaimVerification],
    mapping: dict[str, str],
) -> list[ClaimVerification]:
    return [
        claim.model_copy(
            update={
                "evidence_ids": [mapping[item] for item in claim.evidence_ids if item in mapping],
                "cited_evidence_ids": [
                    mapping[item] for item in claim.cited_evidence_ids if item in mapping
                ],
            }
        )
        for claim in claims
    ]


def _map_expert_result(
    expert: ExpertName,
    response: DomainInferenceResponse,
    all_sources: list[AskSource],
) -> AskExpertResult:
    local_to_global: dict[str, str] = {}
    mapped_sources: list[AskSource] = []
    for item in response.evidence:
        citation_id = f"S{len(all_sources) + 1}"
        local_to_global[item.citation_id] = citation_id
        source = AskSource(
            expert=expert,
            citation_id=citation_id,
            passage_id=item.passage_id,
            document_id=item.document_id,
            document_name=item.document_name,
            excerpt=item.excerpt,
            source=item.source,
        )
        all_sources.append(source)
        mapped_sources.append(source)

    return AskExpertResult(
        expert=expert,
        status="complete",
        answer=_remap_citations(response.answer, local_to_global),
        alignment_status=response.alignment_status,
        source_grounding_supported=response.source_grounding_supported,
        source_grounding_note=response.source_grounding_note,
        evidence=mapped_sources,
        claim_verifications=_remap_claims(response.claim_verifications, local_to_global),
        claim_verification_status=response.claim_verification_status,
        independent_verification_performed=response.independent_verification_performed,
        claim_verification_note=response.claim_verification_note,
        limitations=list(response.limitations),
    )


def _expert_error(expert: ExpertName, message: str) -> AskExpertResult:
    return AskExpertResult(
        expert=expert,
        status="error",
        answer="This expert could not complete the request.",
        source_grounding_supported=False,
        source_grounding_note="No source-grounded answer was returned.",
        limitations=[message],
        error=message,
    )


async def _assess_synthesized_answer(
    answer: str,
    sources: list[AskSource],
    *,
    client: NugenClient,
    settings: Settings,
) -> tuple[list[ClaimVerification], str, bool, str]:
    claim_verifier: ClaimVerifier = get_claim_verifier(client=client, settings=settings)
    if isinstance(claim_verifier, NugenClaimVerifier) and (
        claim_verifier.model_id == (settings.nugen_test_model_id or "").strip()
    ):
        claim_verifier = UnconfiguredClaimVerifier(
            "The claim verifier must use a different model from the synthesis model."
        )
    claims, summary, note = await verify_answer_claims(
        answer,
        [_to_retrieved_passage(source) for source in sources],
        claim_verifier,
    )
    independent = any(item.assessment_basis == "independent_verifier" for item in claims)
    return claims, summary, independent, note


async def _call_expert(
    expert: ExpertName,
    question: str,
    applicable_date: date | None,
    *,
    client: NugenClient,
    settings: Settings,
    repository: SourceDocumentRepository,
    content_store: LocalSourceContentStore,
    alignment_repository: AlignmentJobRepository,
    claim_verifier: ClaimVerifier,
) -> tuple[ExpertName, DomainInferenceResponse | None, str | None, int]:
    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            run_domain_inference(
                DomainInferenceRequest(
                    domain=expert,
                    question=question,
                    applicable_as_of=applicable_date,
                ),
                client=client,
                settings=settings,
                repository=repository,
                content_store=content_store,
                alignment_repository=alignment_repository,
                claim_verifier=claim_verifier,
            ),
            timeout=settings.ask_expert_timeout_seconds,
        )
        return expert, response, None, round((time.perf_counter() - started) * 1000)
    except asyncio.TimeoutError:
        return (
            expert,
            None,
            f"The {expert} expert exceeded the configured request timeout.",
            round((time.perf_counter() - started) * 1000),
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "The request was rejected safely."
        return expert, None, f"The {expert} expert could not answer: {detail}", round((time.perf_counter() - started) * 1000)
    except (NugenAPIError, NugenConfigurationError, SourceDocumentStoreError) as exc:
        return expert, None, f"The {expert} expert could not answer: {exc}", round((time.perf_counter() - started) * 1000)
    except Exception:
        return (
            expert,
            None,
            f"The {expert} expert failed unexpectedly; sensitive request details were not returned.",
            round((time.perf_counter() - started) * 1000),
        )


@router.post("/ask", response_model=AskResponse)
async def ask_question(
    request: AskRequest,
    client: NugenClient = Depends(get_ask_client),
    settings: Settings = Depends(get_settings),
    repository: SourceDocumentRepository = Depends(get_document_repository),
    content_store: LocalSourceContentStore = Depends(get_source_content_store),
    alignment_repository: AlignmentJobRepository = Depends(get_alignment_job_repository),
    interaction_repository: InteractionRepository = Depends(get_interaction_repository),
    _rate_limit: None = Depends(enforce_api_rate_limit),
) -> AskResponse:
    """Route one question, query selected experts concurrently, and synthesize safely."""

    total_started = time.perf_counter()
    request_id = uuid4().hex
    if request.experts is None:
        route_started = time.perf_counter()
        routing: RouterDecision = await route(
            request.question,
            client=client,
            settings=settings,
        )
        routing_ms = round((time.perf_counter() - route_started) * 1000)
        selected_experts = routing.experts
        routing_payload = AskRoutingDecision(
            experts=selected_experts,
            reason=routing.reason,
            routing_method=routing.routing_method,
        )
    else:
        routing_ms = 0
        selected_experts = request.experts
        routing_payload = AskRoutingDecision(
            experts=selected_experts,
            reason="Experts explicitly selected by the requester.",
            routing_method="override",
        )

    try:
        await interaction_repository.save_query(
            QueryLogRecord(
                id=request_id,
                question=request.question,
                routing=routing_payload,
                timestamp=datetime.now(timezone.utc),
            )
        )
    except InteractionStoreError:
        raise HTTPException(
            status_code=503,
            detail="The request could not be recorded; no expert inference was started.",
        ) from None

    if not selected_experts:
        total_ms = round((time.perf_counter() - total_started) * 1000)
        return AskResponse(
            request_id=request_id,
            question=request.question,
            routing=routing_payload,
            experts=[],
            final_answer=routing_payload.reason,
            sources=[],
            gaps=[],
            latency_ms=AskStageLatency(
                routing_ms=routing_ms,
                experts_ms=0,
                synthesis_ms=0,
                total_ms=total_ms,
            ),
        )

    claim_verifier = get_claim_verifier(client=client, settings=settings)
    experts_started = time.perf_counter()
    run_results = await asyncio.gather(
        *(
            _call_expert(
                expert,
                request.question,
                request.applicable_date,
                client=client,
                settings=settings,
                repository=repository,
                content_store=content_store,
                alignment_repository=alignment_repository,
                claim_verifier=claim_verifier,
            )
            for expert in selected_experts
        )
    )
    experts_ms = round((time.perf_counter() - experts_started) * 1000)
    per_expert_ms = {expert: elapsed_ms for expert, _response, _error, elapsed_ms in run_results}

    sources: list[AskSource] = []
    expert_results: list[AskExpertResult] = []
    for expert, response, error, _elapsed in run_results:
        if response is None:
            expert_results.append(_expert_error(expert, error or "Expert result unavailable."))
        else:
            expert_results.append(_map_expert_result(expert, response, sources))

    gaps: list[str] = []
    for result in expert_results:
        if result.error:
            gaps.append(result.error)
        if not result.source_grounding_supported and result.status == "complete":
            gaps.append(f"{result.expert.title()}: {result.source_grounding_note}")
        gaps.extend(result.limitations)
    gaps = list(dict.fromkeys(gaps))

    grounded_results = [
        result
        for result in expert_results
        if result.status == "complete" and result.source_grounding_supported
    ]
    synthesis_ms = 0
    final_claims: list[ClaimVerification] = []
    final_claim_status = "not_performed"
    final_independent = False
    final_claim_note = "No synthesized factual claims were assessed."
    final_grounding = False
    final_grounding_note = "No source-grounded expert output was available."

    if not grounded_results:
        final_answer = (
            "The selected experts did not return an answer supported by retrieved source passages. "
            "Review the expert status and gaps before relying on any information."
        )
    elif len(grounded_results) == 1:
        selected = grounded_results[0]
        final_answer = selected.answer
        final_grounding = selected.source_grounding_supported
        final_grounding_note = selected.source_grounding_note
        final_claims = selected.claim_verifications
        final_claim_status = selected.claim_verification_status
        final_independent = selected.independent_verification_performed
        final_claim_note = selected.claim_verification_note
    else:
        synthesis_started = time.perf_counter()
        try:
            draft = await synthesize(
                request.question,
                grounded_results,
                sources,
                client=client,
                settings=settings,
            )
            citations_valid, citation_note = _validate_citations(draft, sources)
            if not citations_valid:
                final_answer = (
                    "The synthesized wording was withheld because its citation markers did not "
                    "all map to expert source passages. Review the individual expert results below."
                )
                gaps.append(citation_note)
                final_grounding_note = citation_note
            else:
                final_claims, final_claim_status, final_independent, final_claim_note = (
                    await _assess_synthesized_answer(
                        draft,
                        sources,
                        client=client,
                        settings=settings,
                    )
                )
                if final_claim_status == "contains_contradiction" or final_claim_note.startswith(
                    "The verifier failed or returned malformed output"
                ):
                    final_answer = (
                        "The synthesized wording was withheld because claim verification found a "
                        "contradiction or could not complete safely. Review the per-expert answers, "
                        "claim assessments, and evidence below."
                    )
                    final_grounding_note = "The draft citations mapped, but its claim assessment failed safely."
                    gaps.append(final_claim_note)
                else:
                    final_answer = draft
                    final_grounding = True
                    final_grounding_note = citation_note
        except Exception:
            final_answer = "\n\n".join(
                f"### {result.expert.title()} expert\n{result.answer}"
                for result in grounded_results
            )
            final_answer += (
                "\n\nSynthesis was unavailable; the source-grounded individual expert answers "
                "are shown above."
            )
            gaps.append("Synthesis failed; individual expert answers are shown instead.")
            final_grounding = all(result.source_grounding_supported for result in grounded_results)
            final_grounding_note = "Individual expert citations were retained and mapped to returned source passages."
            final_claims = [
                claim
                for result in grounded_results
                for claim in result.claim_verifications
            ]
            final_claim_status = "partially_supported" if final_claims else "not_performed"
            final_independent = any(
                result.independent_verification_performed for result in grounded_results
            )
            final_claim_note = "Individual expert claim assessments are attached above; synthesis was not independently assessed."
        synthesis_ms = round((time.perf_counter() - synthesis_started) * 1000)

    if final_grounding and not _validate_citations(final_answer, sources)[0]:
        final_answer = (
            "The final wording was withheld because at least one citation marker did not map to "
            "a returned source passage. Review the expert results and evidence below."
        )
        final_grounding = False
        final_grounding_note = "Citation validation failed; final wording was withheld."
        final_claims = []
        final_claim_status = "not_performed"
        final_independent = False
        final_claim_note = "No final claims are presented as verified after citation validation failed."

    total_ms = round((time.perf_counter() - total_started) * 1000)
    return AskResponse(
        request_id=request_id,
        question=request.question,
        routing=routing_payload,
        experts=expert_results,
        final_answer=final_answer,
        final_answer_source_grounding_supported=final_grounding,
        final_answer_source_grounding_note=final_grounding_note,
        final_answer_claim_verifications=final_claims,
        final_answer_claim_verification_status=final_claim_status,
        final_answer_independent_verification_performed=final_independent,
        final_answer_claim_verification_note=final_claim_note,
        sources=sources,
        gaps=list(dict.fromkeys(gaps)),
        latency_ms=AskStageLatency(
            routing_ms=routing_ms,
            experts_ms=experts_ms,
            per_expert_ms=per_expert_ms,
            synthesis_ms=synthesis_ms,
            total_ms=total_ms,
        ),
    )
