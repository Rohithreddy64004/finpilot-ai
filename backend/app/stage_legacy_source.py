"""Stage a verified legacy corpus file under its registered document ID."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from backend.app.config import get_settings
from backend.app.schemas import ExpertName
from backend.app.source_content_store import LocalSourceContentStore
from backend.app.source_documents import (
    MongoSourceDocumentRepository,
    SourceDocumentStoreError,
)


async def stage_legacy_source(
    domain: ExpertName,
    document_id: str,
    file_path: Path,
    *,
    repository: MongoSourceDocumentRepository | None = None,
    content_store: LocalSourceContentStore | None = None,
) -> int:
    """Copy no data to Nugen; store local bytes only after metadata checks."""

    settings = get_settings() if repository is None or content_store is None else None
    local_repository = repository or MongoSourceDocumentRepository(settings)
    local_content_store = content_store or LocalSourceContentStore(settings)
    try:
        record = await local_repository.get(domain, document_id)
        if record is None:
            raise ValueError("The document ID is not registered in the requested domain.")

        try:
            content = file_path.read_bytes()
        except OSError:
            raise ValueError("The source file could not be read.") from None
        if len(content) != record.size_bytes:
            raise ValueError(
                "The source file byte count does not match the registered document metadata. "
                f"Expected {record.size_bytes} bytes; received {len(content)}."
            )
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("The source file is not valid UTF-8 text.") from None
        if not decoded.strip():
            raise ValueError("The source file contains no readable text.")

        await local_content_store.save(domain, document_id, content)
        print(f"Verified local source bytes staged for document {document_id} ({domain}).")
        return 0
    finally:
        if repository is None:
            await local_repository.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stage a legacy local source file under an already registered document ID. "
            "This command does not call Nugen."
        )
    )
    parser.add_argument("--domain", choices=("tax", "investment", "insurance"), required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--file", type=Path, required=True)
    arguments = parser.parse_args()

    try:
        return asyncio.run(
            stage_legacy_source(
                arguments.domain,
                arguments.document_id,
                arguments.file,
            )
        )
    except (ValueError, OSError, SourceDocumentStoreError) as exc:
        print(f"Unable to stage source: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
