"""Seed registered, READY document bytes from the committed corpus, locally only."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from backend.app.evidence_retrieval import EXPECTED_CORPUS_FILES
from backend.app.schemas import ExpertName
from backend.app.source_content_store import (
    LocalSourceContentStore,
    SourceContentStoreError,
)
from backend.app.source_documents import SourceDocumentRepository

_DOMAINS: tuple[ExpertName, ...] = ("tax", "investment", "insurance")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SeedContentSummary:
    """Counts only; never includes document content, IDs, or credentials."""

    seeded: int = 0
    already_present: int = 0
    skipped_not_ready: int = 0
    skipped_size_mismatch: int = 0
    skipped_invalid_text: int = 0
    skipped_missing_file: int = 0
    storage_errors: int = 0


async def seed_registered_content(
    repository: SourceDocumentRepository,
    content_store: LocalSourceContentStore,
    *,
    corpus_root: Path | None = None,
) -> SeedContentSummary:
    """Copy matching corpus files for READY records that have no local bytes.

    This reads the local Mongo registry and writes only to the local content
    store. It never calls Nugen, changes document records, or overwrites bytes.
    A source file is eligible only when its UTF-8 bytes match the exact size
    recorded for the READY document.
    """

    root = corpus_root or (_PROJECT_ROOT / "knowledge_bases")
    counts = {field: 0 for field in SeedContentSummary.__dataclass_fields__}
    page_size = 500

    for domain in _DOMAINS:
        offset = 0
        while True:
            records = await repository.list(domain, limit=page_size, offset=offset)
            for record in records:
                if record.original_filename != EXPECTED_CORPUS_FILES[domain]:
                    continue
                if record.nugen_status != "READY":
                    counts["skipped_not_ready"] += 1
                    continue

                try:
                    existing = await content_store.get(domain, record.document_id)
                except SourceContentStoreError:
                    counts["storage_errors"] += 1
                    continue
                if existing is not None:
                    if len(existing) == record.size_bytes:
                        counts["already_present"] += 1
                    else:
                        counts["skipped_size_mismatch"] += 1
                    continue

                source_path = root / EXPECTED_CORPUS_FILES[domain]
                try:
                    content = await asyncio.to_thread(source_path.read_bytes)
                except OSError:
                    counts["skipped_missing_file"] += 1
                    continue
                if len(content) != record.size_bytes:
                    counts["skipped_size_mismatch"] += 1
                    continue
                try:
                    decoded = content.decode("utf-8-sig")
                except UnicodeDecodeError:
                    counts["skipped_invalid_text"] += 1
                    continue
                if not decoded.strip():
                    counts["skipped_invalid_text"] += 1
                    continue
                try:
                    await content_store.save(domain, record.document_id, content)
                except SourceContentStoreError:
                    # A concurrent seed may have filled the path. Never replace it.
                    try:
                        raced = await content_store.get(domain, record.document_id)
                    except SourceContentStoreError:
                        raced = None
                    if raced is not None and len(raced) == record.size_bytes:
                        counts["already_present"] += 1
                    else:
                        counts["storage_errors"] += 1
                    continue
                counts["seeded"] += 1

            if len(records) < page_size:
                break
            offset += len(records)

    return SeedContentSummary(**counts)
