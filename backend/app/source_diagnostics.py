"""Read-only diagnostics for the explicitly active local source documents."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from backend.app.corpus_quality import inspect_corpus_text
from backend.app.evidence_retrieval import EXPECTED_CORPUS_FILES
from backend.app.schemas import ExpertName
from backend.app.source_content_store import LocalSourceContentStore
from backend.app.source_documents import SourceDocumentRepository


DOMAINS: tuple[ExpertName, ...] = ("tax", "investment", "insurance")
_HEADING = re.compile(r"(?im)^\s*(?:\d+\.\s+|#{1,6}\s+)(?P<title>[^\r\n]+)")
_INSTRUCTION_HEADING = re.compile(
    r"how\s+finpilot\s+should|document\s+maintenance|"
    r"assistant\s+instructions|you\s+are\s+finpilot\s+ai",
    re.IGNORECASE,
)
_HEADER_DATE = re.compile(
    r"^\s*(?:Accessed|Retrieved(?:\s+and\s+checked)?|Checked)\s*:\s*"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s*$",
    re.IGNORECASE,
)


async def _read_local_bytes(
    domain: ExpertName,
    document_id: str,
    filename: str,
    content_store: LocalSourceContentStore,
    corpus_root: Path,
) -> tuple[bytes | None, str | None]:
    try:
        content = await content_store.get(domain, document_id)
    except Exception as exc:
        return None, f"local content read failed ({type(exc).__name__})"
    if content is not None:
        return content, None
    if filename == EXPECTED_CORPUS_FILES[domain]:
        path = corpus_root / filename
        try:
            return await asyncio.to_thread(path.read_bytes), None
        except OSError:
            pass
    return None, None


def _retrieved_date_in_header(text: str) -> str | None:
    for line in text.splitlines()[:30]:
        match = _HEADER_DATE.fullmatch(line)
        if match:
            return match.group("date")
    return None


def _has_instruction_heading(text: str) -> bool:
    return any(
        _INSTRUCTION_HEADING.search(match.group("title"))
        for match in _HEADING.finditer(text)
    )


async def diagnose_active_sources(
    repository: SourceDocumentRepository,
    content_store: LocalSourceContentStore,
    *,
    corpus_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Report active IDs, sizes, local text quality, and header dates without provider calls."""

    root = corpus_root or Path(__file__).resolve().parents[2] / "knowledge_bases"
    domains: list[dict[str, Any]] = []
    for domain in DOMAINS:
        selection = await repository.get_active_source_selection(domain)
        document_ids = list(selection.active_document_ids) if selection else []
        documents: list[dict[str, Any]] = []
        for document_id in document_ids:
            record = await repository.get(domain, document_id)
            if record is None:
                documents.append(
                    {
                        "document_id": document_id,
                        "registered_byte_count": None,
                        "local_content_exists": False,
                        "local_byte_count": None,
                        "instruction_style_heading": False,
                        "retrieved_on_header": None,
                        "quality_issues": ["active document is not registered in this domain"],
                    }
                )
                continue

            content, read_issue = await _read_local_bytes(
                domain,
                document_id,
                record.original_filename,
                content_store,
                root,
            )
            issues: list[str] = []
            if content is None:
                issues.append(read_issue or "local content is missing")
                decoded = None
            elif len(content) != record.size_bytes:
                issues.append(
                    f"local byte count {len(content)} does not match registered byte count {record.size_bytes}"
                )
                try:
                    decoded = content.decode("utf-8-sig")
                except UnicodeDecodeError:
                    decoded = None
                    issues.append("local text is not valid UTF-8")
            else:
                try:
                    decoded = content.decode("utf-8-sig")
                except UnicodeDecodeError:
                    decoded = None
                    issues.append("local text is not valid UTF-8")

            instruction_heading = _has_instruction_heading(decoded) if decoded is not None else False
            retrieved_date = _retrieved_date_in_header(decoded) if decoded is not None else None
            if decoded is not None:
                _, content_issues = inspect_corpus_text(
                    record.original_filename,
                    decoded,
                    domain=domain,
                )
                issues.extend(content_issues)
            documents.append(
                {
                    "document_id": document_id,
                    "registered_byte_count": record.size_bytes,
                    "local_content_exists": content is not None,
                    "local_byte_count": len(content) if content is not None else None,
                    "instruction_style_heading": instruction_heading,
                    "retrieved_on_header": retrieved_date,
                    "quality_issues": issues,
                }
            )
        if not document_ids:
            status = "missing"
            issues = [f"No active source set is selected for {domain}."]
        else:
            issues = [
                f"{item['document_id']}: {issue}"
                for item in documents
                for issue in item["quality_issues"]
            ]
            status = "invalid" if issues else "ready"
        domains.append(
            {
                "domain": domain,
                "active_document_ids": document_ids,
                "documents": documents,
                "status": status,
                "issues": issues,
            }
        )
    return domains
