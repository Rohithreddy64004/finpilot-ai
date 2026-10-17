from datetime import date, datetime, timezone

import pytest

from backend.app.schemas import ActiveSourceSelection, SourceDocumentRecord
from backend.app.source_diagnostics import diagnose_active_sources


class MemoryRepository:
    def __init__(self, record: SourceDocumentRecord | None, document_id: str | None) -> None:
        self.record = record
        self.selection = (
            ActiveSourceSelection(
                domain="tax",
                active_document_ids=[document_id],
                revision=1,
                changed_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
            )
            if document_id
            else None
        )

    async def get_active_source_selection(self, domain):
        return self.selection if domain == "tax" else None

    async def get(self, domain, document_id):
        if self.record and domain == "tax" and document_id == self.record.document_id:
            return self.record
        return None


class MemoryContentStore:
    def __init__(self, content: bytes | None) -> None:
        self.content = content

    async def get(self, domain, document_id):
        return self.content


def _record(content: bytes) -> SourceDocumentRecord:
    return SourceDocumentRecord(
        document_id="tax-doc-fixture",
        domain="tax",
        document_name="Tax source fixture",
        original_filename="tax_expert.txt",
        source_url="https://www.incometax.gov.in/",
        retrieved_on=date(2026, 10, 17),
        uploaded_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
        size_bytes=len(content),
    )


@pytest.mark.asyncio
async def test_diagnostics_report_active_document_size_quality_heading_and_header_date(tmp_path) -> None:
    text = (
        "# Tax source\n"
        "Source: https://www.incometax.gov.in/\n"
        "Accessed: 2026-10-17\n\n"
        "1. HOW FINPILOT SHOULD ANSWER TAX QUESTIONS\n"
        "Source: https://www.incometax.gov.in/\n"
        "Accessed: 2026-10-17\n"
        "Source title: Tax education\n"
        "Publisher: Income Tax Department\n"
        + ("Tax facts. " * 1_600)
    )
    content = text.encode("utf-8")
    record = _record(content)
    result = await diagnose_active_sources(
        MemoryRepository(record, record.document_id),
        MemoryContentStore(content),
        corpus_root=tmp_path,
    )

    tax = result[0]
    assert tax["active_document_ids"] == [record.document_id]
    assert tax["documents"][0]["registered_byte_count"] == len(content)
    assert tax["documents"][0]["local_content_exists"] is True
    assert tax["documents"][0]["local_byte_count"] == len(content)
    assert tax["documents"][0]["instruction_style_heading"] is True
    assert tax["documents"][0]["retrieved_on_header"] == "2026-10-17"
    assert tax["status"] == "invalid"
    assert any("assistant instruction" in issue for issue in tax["issues"])
    assert result[1]["status"] == "missing"


@pytest.mark.asyncio
async def test_diagnostics_report_missing_local_content_without_provider_calls(tmp_path) -> None:
    content = b"x" * 700
    record = _record(content)
    result = await diagnose_active_sources(
        MemoryRepository(record, record.document_id),
        MemoryContentStore(None),
        corpus_root=tmp_path,
    )

    assert result[0]["documents"][0]["local_content_exists"] is False
    assert "local content is missing" in result[0]["documents"][0]["quality_issues"]
    assert result[0]["status"] == "invalid"
