"""Conservative per-claim verification interface for retrieved evidence."""

from __future__ import annotations

import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from backend.app.evidence_retrieval import RetrievedPassage, citation_reference_ids
from backend.app.schemas import ClaimStatus, ClaimVerification, ClaimVerificationSummaryStatus


_SENTENCE_SPLIT = re.compile(
    r"(?<=[.!?])\s+(?!\[S\d+\])|(?<=\])\s+(?=[A-Z])|\n+"
)
_CITATION = re.compile(r"\[S\d+\]")
_CITATION_LIKE = re.compile(r"\[S[^\]\r\n]*\]")
_LIST_PREFIX = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_MARKDOWN_HEADING = re.compile(r"^\s*#{1,6}(?:\s+.*)?$")
_FORMAT_ONLY = re.compile(r"^(?:[-*+]+|\d+[.)]|#{1,6}|[-*_]{3,})$")
_TEXT_TOKENS = re.compile(r"[^\W_]+", re.UNICODE)
_NUMERIC_VALUE = re.compile(
    r"(?<![\w])(?P<currency>₹|Rs\.?\s*)?\s*"
    r"(?P<value>\d[\d,]*(?:\.\d+)?)(?P<percent>\s*%)?",
    re.IGNORECASE,
)
_FINANCIAL_NUMBER = re.compile(
    r"(?:₹|Rs\.?\s*)\s*\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?\s*%",
    re.IGNORECASE,
)
_ANSWER_FRAMING = re.compile(
    r"^(?:(?:based on|according to)\s+(?:the\s+)?(?:provided|cited|retrieved)\s+"
    r"(?:source\s+)?(?:passages?|evidence)|in summary|in conclusion|please note that)\s*[:,]?\s*",
    re.IGNORECASE,
)
_NON_FACTUAL_DISCLAIMER = re.compile(
    r"^(?:this (?:answer|response|information) is (?:for )?(?:general )?education|"
    r"this is (?:for )?(?:general )?(?:educational purposes|educational (?:information|content)|education)|"
    r"this is not (?:(?:personalized|personal|financial|tax|investment|insurance) )*advice|"
    r"consult (?:a|an|your) (?:qualified|licensed|financial|tax|insurance) (?:professional|adviser|advisor)).*[.!?]?$",
    re.IGNORECASE,
)


class ClaimEvidenceQuote(BaseModel):
    """An exact quote that the verifier associates with a claim."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^S[1-9][0-9]*$")
    quote: str = Field(min_length=1, max_length=2_000)


class ClaimAssessment(BaseModel):
    """A verifier result anchored to exact text from retrieved passages."""

    model_config = ConfigDict(extra="forbid")

    status: ClaimStatus
    evidence: list[ClaimEvidenceQuote] = Field(default_factory=list)


class ClaimVerifier(Protocol):
    async def assess(
        self,
        claim: str,
        passages: list[RetrievedPassage],
    ) -> ClaimAssessment | dict[str, object]: ...


class UnconfiguredClaimVerifier:
    """Explicit fail-closed default; no semantic verifier is configured."""

    def __init__(
        self,
        reason: str = (
            "No independent claim verifier is configured. Claims are supported only when their "
            "complete sentence directly matches their own cited passage; paraphrases remain unverified."
        ),
    ) -> None:
        self.reason = reason

    async def assess(
        self,
        claim: str,
        passages: list[RetrievedPassage],
    ) -> ClaimAssessment:
        return ClaimAssessment(
            status="unverified",
            evidence=[],
        )


def split_candidate_claims(answer: str) -> list[tuple[str, list[str]]]:
    """Split rendered answer into conservative sentence-level claim candidates.

    This is sentence segmentation only. It deliberately does not infer that a
    sentence is factual or that lexical overlap entails support.
    """

    candidates: list[tuple[str, list[str]]] = []
    for line in answer.splitlines():
        text = line.strip()
        if not text or _MARKDOWN_HEADING.fullmatch(text):
            continue
        text = _LIST_PREFIX.sub("", text, count=1).strip()
        if not text or _FORMAT_ONLY.fullmatch(text):
            continue
        # A short label ending in a colon is a heading, not a factual claim.
        if text.endswith(":") and not re.search(r"[.!?]\s*(?:\[S\d+\])?$", text):
            continue

        for fragment in _SENTENCE_SPLIT.split(text):
            fragment = fragment.strip()
            if not fragment or _FORMAT_ONLY.fullmatch(fragment):
                continue
            cited_ids = sorted(
                citation_reference_ids(fragment),
                key=lambda item: int(item[1:]),
            )
            claim = _CITATION_LIKE.sub("", fragment)
            claim = re.sub(r"\s+([,.;:!?])", r"\1", claim)
            claim = claim.strip().lstrip("-*# ").strip()
            claim = _ANSWER_FRAMING.sub("", claim).strip()
            if _NON_FACTUAL_DISCLAIMER.fullmatch(claim):
                continue
            if claim:
                candidates.append((claim, cited_ids))
    return candidates


def _summary_status(
    results: list[ClaimVerification],
    *,
    independent_verifier: bool,
) -> ClaimVerificationSummaryStatus:
    if not results:
        return "not_performed"
    statuses = {result.status for result in results}
    if "contradicted" in statuses:
        return "contains_contradiction"
    if statuses == {"supported"}:
        return "fully_verified" if independent_verifier else "evidence_supported"
    if "supported" in statuses:
        return "partially_verified" if independent_verifier else "partially_supported"
    if independent_verifier and not any(
        result.assessment_basis == "independent_verifier" for result in results
    ):
        return "verification_unavailable"
    if not independent_verifier:
        return "verification_unavailable"
    return "unverified"


def _direct_text_matches(
    claim: str,
    passages: list[RetrievedPassage],
) -> list[str]:
    """Find an exact sentence match in passages cited on this claim.

    Sentence boundaries prevent a claim phrase embedded in a negation,
    attribution, or unrelated surrounding statement from counting as support.
    This performs no stemming, stop-word removal, or semantic inference.
    """

    claim_tokens = _TEXT_TOKENS.findall(claim.casefold())
    if not claim_tokens:
        return []
    claim_values = _numeric_signatures(claim)
    matches: list[str] = []
    for passage in passages:
        source_sentences = re.split(r"(?<=[.!?])\s+|\n+", passage.excerpt.strip())
        if any(
            _TEXT_TOKENS.findall(sentence.casefold()) == claim_tokens
            and claim_values.issubset(_numeric_signatures(sentence))
            for sentence in source_sentences
        ):
            matches.append(passage.citation_id)
    return matches


def _numeric_signatures(text: str) -> set[tuple[str, bool, bool]]:
    """Normalize numeric values while retaining explicit currency/rate units."""

    return {
        (
            re.sub(r"\D", "", match.group("value")),
            bool(match.group("currency")),
            bool(match.group("percent")),
        )
        for match in _NUMERIC_VALUE.finditer(text)
    }


def all_numeric_claims_have_cited_values(
    answer: str,
    passages: list[RetrievedPassage],
) -> bool:
    """Require each literal rate/amount to occur in its line's cited text.

    This is only a literal-value guard. It does not establish that a number is
    semantically attached to the right tax band or that the source is current.
    """

    known_passages = {passage.citation_id: passage for passage in passages}
    for line in answer.splitlines():
        # Validate values in headings and table rows too, not just prose claims.
        line = re.sub(r"^\s*#{1,6}\s*", "", line)
        line = _LIST_PREFIX.sub("", line, count=1)
        financial_values = {
            signature
            for signature in _numeric_signatures(line)
            if signature[1] or signature[2]
        }
        if not financial_values or not _FINANCIAL_NUMBER.search(line):
            continue
        cited_ids = sorted(
            citation_reference_ids(line),
            key=lambda item: int(item[1:]),
        )
        cited_passages = [
            known_passages[citation_id]
            for citation_id in cited_ids
            if citation_id in known_passages
        ]
        if not cited_passages:
            return False
        cited_values = set().union(
            *(_numeric_signatures(passage.excerpt) for passage in cited_passages)
        )
        if not financial_values.issubset(cited_values):
            return False
    return True


async def verify_answer_claims(
    answer: str,
    passages: list[RetrievedPassage],
    verifier: ClaimVerifier,
) -> tuple[list[ClaimVerification], ClaimVerificationSummaryStatus, str]:
    """Assess sentence candidates and downgrade any failed or invalid output."""

    candidates = split_candidate_claims(answer)
    if not candidates:
        return [], "not_performed", "The answer contained no sentence-level claim candidates."

    known_passages = {passage.citation_id: passage for passage in passages}
    independent_verifier = not isinstance(verifier, UnconfiguredClaimVerifier)
    results: list[ClaimVerification] = []
    try:
        for claim, cited_ids in candidates:
            valid_cited_ids = [item for item in cited_ids if item in known_passages]
            cited_passages = [known_passages[item] for item in valid_cited_ids]
            if not cited_passages:
                results.append(
                    ClaimVerification(
                        claim_text=claim,
                        status="unverified",
                        assessment_basis="unavailable",
                        evidence_ids=[],
                        cited_evidence_ids=[],
                        note="No valid evidence citation appears on this claim.",
                    )
                )
                continue

            if isinstance(verifier, UnconfiguredClaimVerifier):
                matching_ids = _direct_text_matches(claim, cited_passages)
                if matching_ids:
                    results.append(
                        ClaimVerification(
                            claim_text=claim,
                            status="supported",
                            assessment_basis="source_text_comparison",
                            evidence_ids=matching_ids,
                            cited_evidence_ids=valid_cited_ids,
                            note="Claim sentence exactly matches a sentence in its cited passage.",
                        )
                    )
                else:
                    results.append(
                        ClaimVerification(
                            claim_text=claim,
                            status="unverified",
                            assessment_basis="source_text_comparison",
                            evidence_ids=[],
                            cited_evidence_ids=valid_cited_ids,
                            note="No direct wording match was found in this claim's cited passage(s).",
                        )
                    )
                continue

            # A verifier sees only passages explicitly cited on this claim.
            raw_assessment = await verifier.assess(claim, cited_passages)
            assessment = ClaimAssessment.model_validate(raw_assessment)
            evidence_ids = [item.evidence_id for item in assessment.evidence]
            if len(evidence_ids) != len(set(evidence_ids)):
                raise ValueError("verifier returned duplicate evidence IDs")
            claim_passages = {passage.citation_id: passage for passage in cited_passages}
            for quote in assessment.evidence:
                passage = claim_passages.get(quote.evidence_id)
                if passage is None:
                    raise ValueError("verifier returned evidence not cited on this claim")
                normalized_quote = " ".join(quote.quote.split())
                normalized_passage = " ".join(passage.excerpt.split())
                if not normalized_quote or normalized_quote not in normalized_passage:
                    raise ValueError("verifier quote was not present in the referenced passage")
            if assessment.status in {"supported", "contradicted"} and not assessment.evidence:
                raise ValueError("verifier omitted quote-backed evidence for a conclusive status")
            if assessment.status == "unverified" and assessment.evidence:
                raise ValueError("unverified claims cannot name supporting evidence")
            if assessment.status == "supported":
                claim_values = _numeric_signatures(claim)
                quoted_values = set().union(
                    *(
                        _numeric_signatures(quote.quote)
                        for quote in assessment.evidence
                    )
                )
                if not claim_values.issubset(quoted_values):
                    results.append(
                        ClaimVerification(
                            claim_text=claim,
                            status="unverified",
                            assessment_basis="independent_verifier",
                            evidence_ids=[],
                            cited_evidence_ids=valid_cited_ids,
                            note=(
                                "The verifier's supporting quote did not contain every numeric value in the claim; "
                                "the claim remains unverified."
                            ),
                        )
                    )
                    continue
            assessment_note = {
                "supported": "A configured verifier assessed this claim as supported and supplied an exact quote from the linked passage.",
                "contradicted": "A configured verifier assessed this claim as contradicted and supplied an exact quote from the linked passage.",
                "unverified": "The verifier could not establish whether the retrieved evidence supports this claim.",
            }[assessment.status]
            results.append(
                ClaimVerification(
                    claim_text=claim,
                    status=assessment.status,
                    assessment_basis="independent_verifier",
                    evidence_ids=evidence_ids,
                    cited_evidence_ids=valid_cited_ids,
                    note=assessment_note,
                )
            )
    except Exception:
        safe_results = [
            ClaimVerification(
                claim_text=claim,
                status="unverified",
                assessment_basis="unavailable",
                evidence_ids=[],
                cited_evidence_ids=[item for item in cited_ids if item in known_passages],
                note="Claim verification failed or returned invalid evidence; this claim is unverified.",
            )
            for claim, cited_ids in candidates
        ]
        return (
            safe_results,
            "unverified",
            "The verifier failed or returned malformed output; all claims are marked unverified.",
        )

    summary = _summary_status(results, independent_verifier=independent_verifier)
    if summary == "fully_verified":
        note = (
            "A separate configured verifier assessed every candidate claim as supported and each "
            "result is anchored to an exact retrieved passage quote. This is an automated model "
            "assessment, not authoritative professional or legal review."
        )
    elif summary == "contains_contradiction":
        note = "At least one claim was assessed as contradicted; the generated answer is withheld."
    elif summary == "partially_verified":
        note = "A separate verifier supported some claims; the remaining claims are unverified."
    elif summary == "evidence_supported":
        note = (
            "Every claim's sentence was found verbatim in its own cited passage. This is a direct source-text "
            "comparison, not independent semantic verification."
        )
    elif summary == "partially_supported":
        note = (
            "Some claim sentences were found verbatim in their own cited passage. Other claims remain unverified "
            "because no direct wording match was found and no independent verifier is configured."
        )
    elif summary == "verification_unavailable":
        note = getattr(
            verifier,
            "reason",
            "No claim had a valid citation for the configured verifier to assess.",
        )
    else:
        note = getattr(
            verifier,
            "reason",
            "No claim was verified; treat all claims as unverified.",
        )
    return results, summary, note
