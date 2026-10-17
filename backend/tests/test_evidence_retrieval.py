from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.evidence_retrieval import (
    RetrievedPassage,
    SourceContentUnavailableError,
    has_tax_slab_details,
    retrieve_domain_passages,
)
from backend.app.schemas import ExpertName, SourceDocumentRecord


PREPARED_TAX_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "knowledge_bases"
    / "prepared"
    / "tax_regime_comparison_ay_2026_27.txt"
)
PREPARED_SEBI_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "knowledge_bases"
    / "prepared"
    / "sebi_saving_investing_basics_2026_10_17.txt"
)
TAX_SOURCE_URL = (
    "https://www.incometax.gov.in/iec/foportal/help/individual/return-applicable-1"
)


class InMemoryContentStore:
    def __init__(self, content_by_id: dict[str, bytes]) -> None:
        self.content_by_id = content_by_id
        self.calls: list[tuple[ExpertName, str]] = []

    async def get(self, domain: ExpertName, document_id: str) -> bytes | None:
        self.calls.append((domain, document_id))
        return self.content_by_id.get(document_id)


def source_record(
    domain: ExpertName,
    document_id: str,
    content: bytes,
    *,
    retrieved_on: date = date(2026, 10, 15),
) -> SourceDocumentRecord:
    source_urls = {
        "tax": TAX_SOURCE_URL,
        "investment": "https://investor.sebi.gov.in/",
        "insurance": "https://irdai.gov.in/policyholders",
    }
    return SourceDocumentRecord(
        document_id=document_id,
        domain=domain,
        document_name=f"{domain.title()} source",
        original_filename="prepared-source.txt",
        source_url=source_urls[domain],
        retrieved_on=retrieved_on,
        uploaded_at=datetime.combine(retrieved_on, datetime.min.time(), tzinfo=timezone.utc),
        size_bytes=len(content),
    )


@pytest.mark.asyncio
async def test_tax_regime_preparation_retrieves_both_ay_2026_27_slab_tables() -> None:
    content = PREPARED_TAX_SOURCE.read_bytes()
    store = InMemoryContentStore({"prepared-tax": content})
    record = source_record("tax", "prepared-tax", content)

    passages = await retrieve_domain_passages(
        "tax",
        "Compare old and new tax regimes for an individual under age 60 for AY 2026-27, including slab rates",
        [record],
        store,
    )

    excerpts = "\n".join(passage.excerpt for passage in passages)
    assert len(content.decode("utf-8").split()) >= 500
    assert "₹2,50,000" in excerpts
    assert "₹4,00,000" in excerpts
    assert all(passage.document_id == "prepared-tax" for passage in passages)
    assert all(str(passage.source.url) == TAX_SOURCE_URL for passage in passages)
    assert all(passage.source.accessed_on == date(2026, 10, 15) for passage in passages)


@pytest.mark.asyncio
async def test_broad_tax_slab_question_ranks_actual_rate_tables_first() -> None:
    content = (Path(__file__).resolve().parents[2] / "knowledge_bases" / "tax_expert.txt").read_bytes()
    store = InMemoryContentStore({"tax-source": content})
    record = source_record("tax", "tax-source", content)

    passages = await retrieve_domain_passages(
        "tax",
        "How do income tax slabs work for individuals?",
        [record],
        store,
        top_k=5,
    )

    assert "OLD REGIME SLABS" in passages[0].excerpt
    assert "NEW REGIME SLABS" in passages[1].excerpt
    assert has_tax_slab_details(passages)
    assert any("HOW THE REGIMES DIFFER" in passage.excerpt for passage in passages)


@pytest.mark.asyncio
async def test_generic_tax_introduction_is_not_usable_slab_evidence() -> None:
    content = (
        b"1. GENERAL TAX INFORMATION\n"
        b"Income tax is calculated using taxable income and may depend on the applicable rules. "
        b"Individuals should check the current official source for details."
    )
    store = InMemoryContentStore({"tax-generic": content})
    record = source_record("tax", "tax-generic", content)

    passages = await retrieve_domain_passages(
        "tax",
        "How do income tax slabs work for individuals?",
        [record],
        store,
    )

    assert passages
    assert not has_tax_slab_details(passages)


@pytest.mark.asyncio
async def test_rebate_thresholds_are_not_mistaken_for_a_slab_table() -> None:
    content = (
        b"1. REBATE INFORMATION\n"
        b"The resident individual may qualify for a rebate at taxable income up to Rs 5,00,000. "
        b"The rebate is up to Rs 12,500, and tax can have a 5% or 20% rate."
    )
    store = InMemoryContentStore({"tax-rebate": content})
    record = source_record("tax", "tax-rebate", content)

    passages = await retrieve_domain_passages(
        "tax",
        "How do income tax slabs work for individuals?",
        [record],
        store,
    )

    assert passages
    assert not has_tax_slab_details(passages)


@pytest.mark.asyncio
async def test_prepared_sebi_source_retrieves_saving_investing_comparison() -> None:
    content = PREPARED_SEBI_SOURCE.read_bytes()
    store = InMemoryContentStore({"prepared-sebi": content})
    record = source_record(
        "investment",
        "prepared-sebi",
        content,
        retrieved_on=date(2026, 10, 17),
    )

    passages = await retrieve_domain_passages(
        "investment",
        "What is the difference between saving and investing? Explain risk, liquidity, returns, and emergency savings.",
        [record],
        store,
        top_k=6,
    )

    excerpts = "\n".join(passage.excerpt for passage in passages).casefold()
    assert len(content.decode("utf-8").split()) >= 500
    assert "savings" in excerpts
    assert "investment" in excerpts
    assert "liquid" in excerpts
    assert "emergency fund" in excerpts
    assert all(passage.document_id == "prepared-sebi" for passage in passages)
    assert all(passage.source.accessed_on == date(2026, 10, 17) for passage in passages)


@pytest.mark.asyncio
async def test_investing_query_retrieves_investment_passage_with_suffix_variant() -> None:
    content = (
        b"1. INVESTMENT EDUCATION\n"
        b"Investment products involve risk and may offer returns over time."
    )
    store = InMemoryContentStore({"investment-variant": content})
    record = source_record("investment", "investment-variant", content)

    passages = await retrieve_domain_passages(
        "investment",
        "What does investing involve?",
        [record],
        store,
    )

    assert passages
    assert passages[0].document_id == "investment-variant"
    assert "Investment products involve risk" in passages[0].excerpt


@pytest.mark.asyncio
async def test_tax_retrieval_never_reads_or_cites_investment_content() -> None:
    tax_content = PREPARED_TAX_SOURCE.read_bytes()
    other_content = b"A mutual fund investment can rise or fall; returns are not guaranteed."
    tax_record = source_record("tax", "tax-document", tax_content)
    investment_record = source_record("investment", "investment-document", other_content)
    store = InMemoryContentStore(
        {"tax-document": tax_content, "investment-document": other_content}
    )

    with pytest.raises(SourceContentUnavailableError, match="another expert domain"):
        await retrieve_domain_passages(
            "tax",
            "old and new income tax slabs for AY 2026-27",
            [tax_record, investment_record],
            store,
        )

    assert store.calls == []
