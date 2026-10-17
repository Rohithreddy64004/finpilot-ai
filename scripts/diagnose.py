"""Read-only diagnostics for active source IDs and their local text copies."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings
from backend.app.source_content_store import LocalSourceContentStore
from backend.app.source_diagnostics import diagnose_active_sources
from backend.app.source_documents import MongoSourceDocumentRepository


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


async def _run() -> int:
    repository = MongoSourceDocumentRepository(get_settings())
    content_store = LocalSourceContentStore(get_settings())
    try:
        domains = await diagnose_active_sources(repository, content_store)
    except Exception as exc:
        # Driver errors can contain connection data. Report only the type.
        print(f"Diagnostics could not read the source registry ({type(exc).__name__}).")
        return 1
    finally:
        await repository.close()

    for domain in domains:
        ids = ", ".join(domain["active_document_ids"]) or "(none)"
        print(f"{domain['domain']}: active_document_ids={ids}")
        if not domain["documents"]:
            print(f"  status={domain['status']}; " + "; ".join(domain["issues"]))
            continue
        for document in domain["documents"]:
            registered = document["registered_byte_count"]
            local = document["local_byte_count"]
            print(
                f"  {document['document_id']}: registered_bytes={registered if registered is not None else '-'}; "
                f"local_content_exists={_yes_no(document['local_content_exists'])}; "
                f"local_bytes={local if local is not None else '-'}; "
                f"instruction_style_heading={_yes_no(document['instruction_style_heading'])}; "
                f"retrieved_on_header={document['retrieved_on_header'] or '-'}"
            )
            if document["quality_issues"]:
                print("    quality_issues=" + "; ".join(document["quality_issues"]))
        print(f"  status={domain['status']}")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
