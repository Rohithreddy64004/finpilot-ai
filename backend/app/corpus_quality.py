"""Shared quality checks for committed and active source text."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit


WORD = re.compile(r"[\wâ‚¹]+(?:[â€™'-][\w]+)*", re.UNICODE)
SOURCE_HEADER = re.compile(r"\b(?:source\s*(?:url)?|official\s+sources?)\b", re.IGNORECASE)
DATE_HEADER = re.compile(
    r"\b(?:accessed|retrieved|checked|access\s+date|prepared\s*:\s*\d+|"
    r"prepared\s+on|prepared\s+for\s+review\s+on)\b",
    re.IGNORECASE,
)
INSTRUCTION_TEXT = re.compile(
    r"\b(?:how\s+finpilot\s+should|document\s+maintenance|"
    r"assistant\s+instructions|you\s+are\s+finpilot\s+ai)\b",
    re.IGNORECASE,
)
SECTION_HEADING = re.compile(r"(?m)^\s*(\d+\.\s+[^\n]+)")
SECTION_BOUNDARY = re.compile(r"(?m)^\s*(?P<title>\d+\.\s+[^\n]+)\s*$")
SECTION_SOURCE = re.compile(r"^Source:\s*(\S+)\s*$", re.IGNORECASE)
SECTION_DATE = re.compile(r"^Accessed:\s*(\d{4}-\d{2}-\d{2})\s*$", re.IGNORECASE)
SECTION_TITLE = re.compile(r"^Source title:\s*(.+?)\s*$", re.IGNORECASE)
SECTION_PUBLISHER = re.compile(r"^Publisher:\s*(.+?)\s*$", re.IGNORECASE)
OFFICIAL_HOSTS = {
    "tax": ("incometax.gov.in", "incometaxindia.gov.in"),
    "investment": ("sebi.gov.in",),
    "insurance": ("irdai.gov.in",),
}


def inspect_corpus_text(
    filename: str,
    text: str,
    *,
    domain: str | None = None,
) -> tuple[int, list[str]]:
    """Return the same word-count and provenance issues for any in-memory text."""

    words = len(WORD.findall(text))
    issues: list[str] = []
    header = "\n".join(text.splitlines()[:30])
    if not SOURCE_HEADER.search(header):
        issues.append("missing source header")
    if not DATE_HEADER.search(header):
        issues.append("missing access or retrieval date")
    minimum_words = 1_500 if filename.endswith("_expert.txt") else 500
    if words < minimum_words:
        issues.append(f"under {minimum_words} words ({words})")
    instruction_matches = list(INSTRUCTION_TEXT.finditer(text))
    if instruction_matches:
        headings = list(SECTION_HEADING.finditer(text))
        sections = sorted(
            {
                next(
                    (heading.group(1).strip() for heading in reversed(headings)
                     if heading.start() <= match.start()),
                    "unsectioned text",
                )
                for match in instruction_matches
            }
        )
        issues.append(
            "contains assistant instruction or document-maintenance text in: "
            + ", ".join(sections)
        )

    expert_domain = domain or Path(filename).stem.removesuffix("_expert")
    if expert_domain in OFFICIAL_HOSTS:
        headings = list(SECTION_BOUNDARY.finditer(text))
        for index, heading in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            section = text[heading.end() : end]
            title = heading.group("title").strip()
            fields = {
                "source": next((match.group(1) for line in section.splitlines()
                                if (match := SECTION_SOURCE.fullmatch(line.strip()))), None),
                "date": next((match.group(1) for line in section.splitlines()
                              if (match := SECTION_DATE.fullmatch(line.strip()))), None),
                "title": next((match.group(1) for line in section.splitlines()
                               if (match := SECTION_TITLE.fullmatch(line.strip()))), None),
                "publisher": next((match.group(1) for line in section.splitlines()
                                    if (match := SECTION_PUBLISHER.fullmatch(line.strip()))), None),
            }
            missing = [key for key, value in fields.items() if not value]
            if missing:
                issues.append(f"{title}: missing section provenance fields ({', '.join(missing)})")
                continue
            try:
                parsed = urlsplit(fields["source"] or "")
                host = (parsed.hostname or "").lower().rstrip(".")
                port = parsed.port
                date.fromisoformat(fields["date"] or "")
                valid_url = (
                    parsed.scheme.lower() == "https"
                    and host
                    and port in (None, 443)
                    and parsed.username is None
                    and parsed.password is None
                    and not parsed.query
                    and not parsed.fragment
                    and any(
                        host == allowed or host.endswith("." + allowed)
                        for allowed in OFFICIAL_HOSTS[expert_domain]
                    )
                )
            except ValueError:
                valid_url = False
            if not valid_url:
                issues.append(f"{title}: source URL or Accessed date is invalid for {expert_domain}")
    return words, issues
