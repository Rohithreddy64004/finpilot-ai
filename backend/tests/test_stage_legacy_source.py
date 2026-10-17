from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backend.app.schemas import SourceDocumentRecord
from backend.app.stage_legacy_source import stage_legacy_source


class MemoryRepository:
    def __init__(self, record: SourceDocumentRecord | None) -> None:
        self.record = record

    async def get(self, domain: str, document_id: str) -> SourceDocumentRecord | None:
        if self.record and self.record.domain == domain and self.record.document_id == document_id:
            return self.record
        return None


class MemoryContentStore:
    def __init__(self) -> None:
        self.contents: dict[tuple[str, str], bytes] = {}

    async def save(self, domain: str, document_id: str, content: bytes) -> None:
        self.contents[(domain, document_id)] = content


def registered_record(document_id: str, content: bytes) -> SourceDocumentRecord:
    return SourceDocumentRecord(
        document_id=document_id,
        domain="tax",
        document_name="Original Income Tax Department source",
        original_filename="tax_expert.txt",
        source_url="https://www.incometax.gov.in/iec/foportal/",
        retrieved_on=date(2026, 10, 15),
        uploaded_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
        size_bytes=len(content),
    )


@pytest.mark.asyncio
async def test_legacy_stage_copies_only_matching_utf8_bytes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    content = "Original Tax source retained for rollback. " * 40
    raw = content.encode("utf-8")
    source_file = tmp_path / "tax_expert.txt.backup"
    source_file.write_bytes(raw)
    repository = MemoryRepository(registered_record("old-tax-id", raw))
    store = MemoryContentStore()

    exit_code = await stage_legacy_source(
        "tax",
        "old-tax-id",
        source_file,
        repository=repository,  # type: ignore[arg-type]
        content_store=store,  # type: ignore[arg-type]
    )

    assert exit_code == 0
    assert store.contents[("tax", "old-tax-id")] == raw
    assert "old-tax-id" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_legacy_stage_rejects_size_mismatch_without_writing(tmp_path: Path) -> None:
    source_file = tmp_path / "tax_expert.txt.backup"
    source_file.write_text("Different source bytes", encoding="utf-8")
    repository = MemoryRepository(registered_record("old-tax-id", b"registered bytes"))
    store = MemoryContentStore()

    with pytest.raises(ValueError, match="byte count does not match"):
        await stage_legacy_source(
            "tax",
            "old-tax-id",
            source_file,
            repository=repository,  # type: ignore[arg-type]
            content_store=store,  # type: ignore[arg-type]
        )

    assert store.contents == {}
