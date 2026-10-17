"""Seed missing local retrieval content from registered READY Mongo records."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings
from backend.app.content_seeding import seed_registered_content
from backend.app.source_content_store import LocalSourceContentStore
from backend.app.source_documents import MongoSourceDocumentRepository


async def _run() -> int:
    settings = get_settings()
    repository = MongoSourceDocumentRepository(settings)
    try:
        summary = await seed_registered_content(
            repository,
            LocalSourceContentStore(settings),
        )
        print("Source content seed summary (counts only):")
        for name, value in summary.__dict__.items():
            print(f"  {name}: {value}")
        return 1 if summary.storage_errors else 0
    except Exception as exc:
        # Do not print exception text: database driver errors can include URIs.
        print(f"Source content seeding failed safely ({type(exc).__name__}).")
        return 1
    finally:
        await repository.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
