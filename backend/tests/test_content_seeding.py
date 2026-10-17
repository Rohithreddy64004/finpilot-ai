from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.content_seeding import seed_registered_content
from backend.app import main as main_module
from backend.app.schemas import SourceDocumentRecord
from backend.app.source_content_store import LocalSourceContentStore


class FakeDocumentRepository:
    def __init__(self, records: list[SourceDocumentRecord]) -> None:
        self.records = records

    async def list(self, domain, *, limit: int, offset: int):
        rows = [record for record in self.records if record.domain == domain]
        return rows[offset : offset + limit]


def make_record(domain: str, filename: str, document_id: str, content: bytes, status="READY"):
    return SourceDocumentRecord(
        document_id=document_id,
        domain=domain,
        document_name=f"{domain.title()} source",
        original_filename=filename,
        source_url="https://example.gov.in/source",
        retrieved_on=date(2026, 10, 17),
        uploaded_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
        size_bytes=len(content),
        nugen_status=status,
    )


@pytest.mark.asyncio
async def test_seed_content_is_idempotent_and_only_seeds_ready_matching_records(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    text = "Tax source content with UTF-8 rupee symbol ₹.\n"
    data = text.encode("utf-8")
    (corpus / "tax_expert.txt").write_bytes(data)
    records = [
        make_record("tax", "tax_expert.txt", "ready-id", data),
        make_record("tax", "tax_expert.txt", "pending-id", data, status="PROCESSING"),
    ]
    repository = FakeDocumentRepository(records)
    store = LocalSourceContentStore(
        Settings(_env_file=None, source_content_directory=str(tmp_path / "store"))
    )

    first = await seed_registered_content(repository, store, corpus_root=corpus)
    second = await seed_registered_content(repository, store, corpus_root=corpus)

    assert first.seeded == 1
    assert first.skipped_not_ready == 1
    assert await store.get("tax", "ready-id") == data
    assert second.seeded == 0
    assert second.already_present == 1
    assert second.skipped_not_ready == 1


@pytest.mark.asyncio
async def test_seed_skips_size_mismatch_and_never_overwrites_existing_content(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = b"new authoritative bytes"
    (corpus / "tax_expert.txt").write_bytes(source)
    mismatch = make_record("tax", "tax_expert.txt", "mismatch", b"wrong stored size")
    overwrite = make_record("tax", "tax_expert.txt", "already", source)
    store = LocalSourceContentStore(
        Settings(_env_file=None, source_content_directory=str(tmp_path / "store"))
    )
    await store.save("tax", "already", b"different existing content")

    result = await seed_registered_content(
        FakeDocumentRepository([mismatch, overwrite]), store, corpus_root=corpus
    )

    assert result.seeded == 0
    assert result.skipped_size_mismatch == 2
    assert await store.get("tax", "mismatch") is None
    assert await store.get("tax", "already") == b"different existing content"


@pytest.mark.asyncio
async def test_seed_skips_invalid_utf8_without_writing(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    invalid = b"\xff\xfe"
    (corpus / "insurance_expert.txt").write_bytes(invalid)
    record = make_record("insurance", "insurance_expert.txt", "invalid", invalid)
    store = LocalSourceContentStore(
        Settings(_env_file=None, source_content_directory=str(tmp_path / "store"))
    )

    result = await seed_registered_content(
        FakeDocumentRepository([record]), store, corpus_root=corpus
    )

    assert result.skipped_invalid_text == 1
    assert await store.get("insurance", "invalid") is None


@pytest.mark.asyncio
async def test_optional_startup_seeding_failure_does_not_stop_service(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FakeRepository:
        def __init__(self, _settings) -> None:
            pass

        async def close(self) -> None:
            return None

    async def fail_seed(*_args, **_kwargs):
        raise RuntimeError("private database connection detail")

    monkeypatch.setattr(
        main_module,
        "settings",
        Settings(_env_file=None, seed_content_on_startup=True),
    )
    monkeypatch.setattr(main_module, "MongoSourceDocumentRepository", FakeRepository)
    monkeypatch.setattr(main_module, "seed_registered_content", fail_seed)

    async with main_module.lifespan(main_module.app):
        assert True

    assert "RuntimeError" in caplog.text
    assert "private database connection detail" not in caplog.text
