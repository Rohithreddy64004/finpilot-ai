from dataclasses import replace

import pytest

from backend.app.claim_verification import (
    ClaimAssessment,
    UnconfiguredClaimVerifier,
    all_numeric_claims_have_cited_values,
    split_candidate_claims,
    verify_answer_claims,
)
from backend.app.evidence_retrieval import RetrievedPassage
from backend.app.schemas import SourceReference


def sample_passage() -> RetrievedPassage:
    return RetrievedPassage(
        citation_id="S1",
        passage_id="fixture-doc:1",
        document_id="fixture-doc",
        document_name="Fixture source",
        excerpt="A systematic investment plan does not guarantee returns.",
        source=SourceReference(
            title="Fixture source",
            publisher="Fixture authority",
            url="https://example.gov.in/source",
        ),
        score=1.0,
    )


class StaticVerifier:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, list[str]]] = []

    async def assess(self, claim: str, passages: list[RetrievedPassage]):
        self.calls.append((claim, [passage.citation_id for passage in passages]))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def second_passage() -> RetrievedPassage:
    return replace(
        sample_passage(),
        citation_id="S2",
        passage_id="fixture-doc:2",
        excerpt="A different statement about investment risk.",
    )


def test_sentence_segmentation_keeps_citation_marker_with_its_claim() -> None:
    assert split_candidate_claims("A SIP does not guarantee returns. [S1] Another fact [S1].") == [
        ("A SIP does not guarantee returns.", ["S1"]),
        ("Another fact.", ["S1"]),
    ]


def test_numbered_and_short_list_items_keep_claims_without_formatting_fragments() -> None:
    answer = """# Overview
##
1. A SIP does not guarantee returns. [S1]
2) Risk varies by investment. [S2]
- Yes. [S1]
- [S1]
3.
---
Investment considerations:
A normal paragraph remains a claim. [S1] Another prose fact. [S2]"""

    assert split_candidate_claims(answer) == [
        ("A SIP does not guarantee returns.", ["S1"]),
        ("Risk varies by investment.", ["S2"]),
        ("Yes.", ["S1"]),
        ("A normal paragraph remains a claim.", ["S1"]),
        ("Another prose fact.", ["S2"]),
    ]


def test_markdown_framing_and_educational_disclaimers_are_not_claims() -> None:
    answer = """## Key points
Based on the provided source passages, diversification reduces concentration risk. [S1]
1. Liquidity is the ability to access money when needed. [S2]
This is not personalized financial advice.
Consult a qualified professional.
"""

    assert split_candidate_claims(answer) == [
        ("diversification reduces concentration risk.", ["S1"]),
        ("Liquidity is the ability to access money when needed.", ["S2"]),
    ]


@pytest.mark.asyncio
async def test_uncited_claim_cannot_borrow_citation_from_another_sentence() -> None:
    verifier = StaticVerifier(
        {
            "status": "supported",
            "evidence": [
                {
                    "evidence_id": "S1",
                    "quote": "A systematic investment plan does not guarantee returns.",
                }
            ],
        }
    )
    claims, summary, _ = await verify_answer_claims(
        "A SIP does not guarantee returns. [S1]\nAn uncited claim remains separate.",
        [sample_passage(), second_passage()],
        verifier,
    )

    assert summary == "partially_verified"
    assert [claim.status for claim in claims] == ["supported", "unverified"]
    assert claims[1].cited_evidence_ids == []
    assert claims[1].evidence_ids == []
    assert claims[1].note == "No valid evidence citation appears on this claim."
    assert verifier.calls == [("A SIP does not guarantee returns.", ["S1"])]


@pytest.mark.asyncio
async def test_verifier_cannot_use_passage_cited_on_another_claim() -> None:
    verifier = StaticVerifier(
        {
            "status": "supported",
            "evidence": [
                {
                    "evidence_id": "S1",
                    "quote": "A systematic investment plan does not guarantee returns.",
                }
            ],
        }
    )
    claims, summary, note = await verify_answer_claims(
        "A SIP does not guarantee returns. [S1]\nA second claim. [S2]",
        [sample_passage(), second_passage()],
        verifier,
    )

    assert summary == "unverified"
    assert all(claim.status == "unverified" for claim in claims)
    assert verifier.calls == [
        ("A SIP does not guarantee returns.", ["S1"]),
        ("A second claim.", ["S2"]),
    ]
    assert "malformed output" in note


@pytest.mark.asyncio
async def test_malformed_claim_marker_is_not_treated_as_valid_evidence() -> None:
    verifier = StaticVerifier(
        {
            "status": "supported",
            "evidence": [
                {
                    "evidence_id": "S1",
                    "quote": "A systematic investment plan does not guarantee returns.",
                }
            ],
        }
    )
    claims, summary, _ = await verify_answer_claims(
        "A SIP does not guarantee returns. [Sx]",
        [sample_passage()],
        verifier,
    )

    assert summary == "verification_unavailable"
    assert claims[0].claim_text == "A SIP does not guarantee returns."
    assert claims[0].cited_evidence_ids == []
    assert claims[0].status == "unverified"
    assert verifier.calls == []


@pytest.mark.asyncio
async def test_unconfigured_verifier_returns_explicit_unverified_claims() -> None:
    claims, summary, note = await verify_answer_claims(
        "A SIP does not guarantee returns. [S1]",
        [sample_passage()],
        UnconfiguredClaimVerifier(),
    )

    assert summary == "verification_unavailable"
    assert claims[0].status == "unverified"
    assert claims[0].assessment_basis == "source_text_comparison"
    assert claims[0].evidence_ids == []
    assert claims[0].cited_evidence_ids == ["S1"]
    assert "No independent claim verifier is configured" in note


@pytest.mark.asyncio
async def test_unconfigured_verifier_supports_exact_claim_wording_from_own_citation() -> None:
    exact_passage = replace(
        sample_passage(),
        excerpt=(
            "Diversification can reduce the impact of poor performance in one part of a portfolio, "
            "but it cannot eliminate all risk."
        ),
    )
    claim_text = (
        "Diversification can reduce the impact of poor performance in one part of a portfolio, "
        "but it cannot eliminate all risk."
    )
    claims, summary, note = await verify_answer_claims(
        f"{claim_text} [S1]",
        [exact_passage],
        UnconfiguredClaimVerifier(),
    )

    assert summary == "evidence_supported"
    assert claims[0].status == "supported"
    assert claims[0].assessment_basis == "source_text_comparison"
    assert claims[0].evidence_ids == ["S1"]
    assert claims[0].cited_evidence_ids == ["S1"]
    assert "not independent semantic verification" in note


@pytest.mark.asyncio
async def test_unconfigured_exact_word_match_does_not_drop_percent_unit() -> None:
    source = replace(sample_passage(), excerpt="The applicable tax rate is 5.")
    claims, summary, _ = await verify_answer_claims(
        "The applicable tax rate is 5%. [S1]",
        [source],
        UnconfiguredClaimVerifier(),
    )

    assert summary == "verification_unavailable"
    assert claims[0].status == "unverified"
    assert claims[0].evidence_ids == []


@pytest.mark.asyncio
async def test_source_text_comparison_is_exact_ordered_and_claim_local() -> None:
    relevant = replace(
        sample_passage(),
        excerpt="Diversification can reduce the impact of poor performance in one part of a portfolio.",
    )
    unrelated = replace(
        second_passage(),
        excerpt="Do not state that diversification eliminates all risk.",
    )
    answer = (
        "Diversification can reduce the impact of poor performance in one part of a portfolio. [S1]\n"
        "Diversification eliminates all risk. [S1]\n"
        "An uncited claim cannot borrow that passage.\n"
        "Diversification eliminates all risk. [S2]"
    )
    claims, summary, _ = await verify_answer_claims(
        answer,
        [relevant, unrelated],
        UnconfiguredClaimVerifier(),
    )

    assert summary == "partially_supported"
    assert [claim.status for claim in claims] == ["supported", "unverified", "unverified", "unverified"]
    assert claims[0].evidence_ids == ["S1"]
    assert claims[1].evidence_ids == []
    assert claims[1].cited_evidence_ids == ["S1"]
    assert claims[2].assessment_basis == "unavailable"
    assert claims[2].cited_evidence_ids == []
    assert claims[3].evidence_ids == []
    assert claims[3].cited_evidence_ids == ["S2"]


@pytest.mark.asyncio
async def test_source_text_comparison_can_match_multiple_explicit_passages() -> None:
    text = "Diversification can reduce the impact of poor performance."
    first = replace(sample_passage(), excerpt=f"In a portfolio. {text} It cannot eliminate all risk.")
    second = replace(second_passage(), excerpt=f"A broad portfolio helps. {text}")
    claims, summary, _ = await verify_answer_claims(
        f"{text} [S1] [S2]",
        [first, second],
        UnconfiguredClaimVerifier(),
    )

    assert summary == "evidence_supported"
    assert claims[0].evidence_ids == ["S1", "S2"]
    assert claims[0].cited_evidence_ids == ["S1", "S2"]


@pytest.mark.asyncio
async def test_unknown_but_well_formed_citation_does_not_get_source_support() -> None:
    claims, summary, _ = await verify_answer_claims(
        "A systematic investment plan does not guarantee returns. [S99]",
        [sample_passage()],
        UnconfiguredClaimVerifier(),
    )

    assert summary == "verification_unavailable"
    assert claims[0].status == "unverified"
    assert claims[0].assessment_basis == "unavailable"
    assert claims[0].cited_evidence_ids == []


@pytest.mark.asyncio
async def test_configured_verifier_can_report_supported_claim_with_known_evidence() -> None:
    claims, summary, _ = await verify_answer_claims(
        "A SIP does not guarantee returns. [S1]",
        [sample_passage()],
        StaticVerifier(
            {
                "status": "supported",
                "evidence": [
                    {
                        "evidence_id": "S1",
                        "quote": "A systematic investment plan does not guarantee returns.",
                    }
                ],
            }
        ),
    )

    assert summary == "fully_verified"
    assert claims[0].status == "supported"
    assert claims[0].evidence_ids == ["S1"]


@pytest.mark.asyncio
async def test_configured_verifier_cannot_support_a_numeric_claim_with_a_different_quote_value() -> None:
    tax_passage = replace(
        sample_passage(),
        excerpt="A rate of 5% applies to income above ₹2,50,000.",
    )
    claims, summary, _ = await verify_answer_claims(
        "A rate of 9% applies to income above ₹2,50,000. [S1]",
        [tax_passage],
        StaticVerifier(
            {
                "status": "supported",
                "evidence": [
                    {
                        "evidence_id": "S1",
                        "quote": "A rate of 5% applies to income above ₹2,50,000.",
                    }
                ],
            }
        ),
    )

    assert summary == "unverified"
    assert claims[0].status == "unverified"
    assert claims[0].assessment_basis == "independent_verifier"
    assert claims[0].evidence_ids == []
    assert "numeric value" in claims[0].note


@pytest.mark.asyncio
async def test_exact_numeric_source_quote_can_support_claim_when_verifier_is_configured() -> None:
    tax_passage = replace(
        sample_passage(),
        excerpt="A rate of 5% applies to income above ₹2,50,000.",
    )
    claims, summary, _ = await verify_answer_claims(
        "A rate of 5% applies above ₹2,50,000. [S1]",
        [tax_passage],
        StaticVerifier(
            {
                "status": "supported",
                "evidence": [
                    {
                        "evidence_id": "S1",
                        "quote": "A rate of 5% applies to income above ₹2,50,000.",
                    }
                ],
            }
        ),
    )

    assert summary == "fully_verified"
    assert claims[0].status == "supported"
    assert claims[0].evidence_ids == ["S1"]


def test_numeric_guard_requires_values_in_the_claims_own_cited_passage() -> None:
    passage = replace(
        sample_passage(),
        excerpt="A rate of 5% applies to income above ₹2,50,000.",
    )

    assert all_numeric_claims_have_cited_values(
        "A rate of 5% applies above ₹2,50,000. [S1]",
        [passage],
    )
    assert not all_numeric_claims_have_cited_values(
        "A rate of 9% applies above ₹2,50,000. [S1]",
        [passage],
    )
    assert not all_numeric_claims_have_cited_values(
        "A rate of 5% applies above ₹2,50,000.",
        [passage],
    )
    assert not all_numeric_claims_have_cited_values(
        "## The 99% tax rate\n\nSee the source. [S1]",
        [passage],
    )


@pytest.mark.asyncio
async def test_configured_verifier_reports_contradicted_claim_and_known_evidence() -> None:
    claims, summary, note = await verify_answer_claims(
        "A SIP guarantees returns. [S1]",
        [sample_passage()],
        StaticVerifier(
            {
                "status": "contradicted",
                "evidence": [
                    {
                        "evidence_id": "S1",
                        "quote": "A systematic investment plan does not guarantee returns.",
                    }
                ],
            }
        ),
    )

    assert summary == "contains_contradiction"
    assert claims[0].status == "contradicted"
    assert claims[0].evidence_ids == ["S1"]
    assert "contradicted" in note


@pytest.mark.asyncio
async def test_malformed_verifier_output_fails_closed_to_unverified() -> None:
    claims, summary, note = await verify_answer_claims(
        "A SIP guarantees returns. [S1]",
        [sample_passage()],
        StaticVerifier(
            {
                "status": "supported",
                "evidence": [{"evidence_id": "S99", "quote": "Invented evidence."}],
            }
        ),
    )

    assert summary == "unverified"
    assert claims[0].status == "unverified"
    assert claims[0].evidence_ids == []
    assert "malformed output" in note


@pytest.mark.asyncio
async def test_verifier_quote_must_match_the_referenced_passage_exactly() -> None:
    claims, summary, _ = await verify_answer_claims(
        "A SIP guarantees returns. [S1]",
        [sample_passage()],
        StaticVerifier(
            {
                "status": "supported",
                "evidence": [{"evidence_id": "S1", "quote": "SIPs guarantee a return."}],
            }
        ),
    )

    assert summary == "unverified"
    assert claims[0].status == "unverified"
    assert claims[0].evidence_ids == []


@pytest.mark.asyncio
async def test_verifier_exception_fails_closed_to_unverified() -> None:
    claims, summary, note = await verify_answer_claims(
        "A SIP guarantees returns. [S1]",
        [sample_passage()],
        StaticVerifier(RuntimeError("private provider error")),
    )

    assert summary == "unverified"
    assert claims[0].status == "unverified"
    assert "private provider error" not in note
