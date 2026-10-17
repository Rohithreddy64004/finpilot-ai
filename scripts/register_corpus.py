"""Preview or explicitly register the three committed expert corpora.

Without ``--confirm`` this script only reads local text and prints a plan. A
confirmed run talks only to the configured local FinPilot API; that API then
performs the provider upload and active-source switch.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import httpx


ROOT = Path(__file__).resolve().parents[1]
CORPUS_FILES = {
    "tax": "tax_expert.txt",
    "investment": "investment_expert.txt",
    "insurance": "insurance_expert.txt",
}
OFFICIAL_HOSTS = {
    "tax": ("incometax.gov.in", "incometaxindia.gov.in"),
    "investment": ("sebi.gov.in",),
    "insurance": ("irdai.gov.in", "policyholder.gov.in"),
}
SOURCE_LINE = re.compile(r"^Source:\s*(\S+)\s*$", re.IGNORECASE)
DATE_LINE = re.compile(r"^Accessed:\s*(\d{4}-\d{2}-\d{2})\s*$", re.IGNORECASE)
TITLE_LINE = re.compile(r"^Source title:\s*(.+?)\s*$", re.IGNORECASE)
PUBLISHER_LINE = re.compile(r"^Publisher:\s*(.+?)\s*$", re.IGNORECASE)
SECTION_HEADING = re.compile(r"(?m)^\s*(?P<title>\d+\.\s+[^\n]+)\s*$")


@dataclass(frozen=True, slots=True)
class CorpusSource:
    domain: str
    path: Path
    content: bytes
    source_url: str
    retrieved_on: date
    word_count: int


def _official_source(domain: str, url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        return (
            parsed.scheme.lower() == "https"
            and bool(host)
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and not parsed.query
            and not parsed.fragment
            and any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_HOSTS[domain])
        )
    except ValueError:
        return False


def _validate_section_provenance(text: str, domain: str, filename: str) -> None:
    headings = list(SECTION_HEADING.finditer(text))
    if not headings:
        raise ValueError(f"{filename} has no numbered source sections")
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.end() : end]
        section_title = heading.group("title").strip()
        fields = {
            "source": next((m.group(1) for line in section.splitlines()
                            if (m := SOURCE_LINE.fullmatch(line.strip()))), None),
            "date": next((m.group(1) for line in section.splitlines()
                          if (m := DATE_LINE.fullmatch(line.strip()))), None),
            "title": next((m.group(1) for line in section.splitlines()
                           if (m := TITLE_LINE.fullmatch(line.strip()))), None),
            "publisher": next((m.group(1) for line in section.splitlines()
                                if (m := PUBLISHER_LINE.fullmatch(line.strip()))), None),
        }
        if any(value is None for value in fields.values()):
            raise ValueError(f"{filename} section {section_title} has incomplete provenance")
        try:
            date.fromisoformat(fields["date"] or "")
        except ValueError:
            raise ValueError(f"{filename} section {section_title} has an invalid Accessed date") from None
        if not _official_source(domain, fields["source"] or ""):
            raise ValueError(f"{filename} section {section_title} has an invalid official source URL")


def load_corpus_sources(
    *, domain: str | None = None, corpus_root: Path = ROOT / "knowledge_bases"
) -> list[CorpusSource]:
    """Read and validate the chosen committed expert documents without network access."""

    domains = [domain] if domain else list(CORPUS_FILES)
    sources: list[CorpusSource] = []
    for selected_domain in domains:
        if selected_domain not in CORPUS_FILES:
            raise ValueError("domain must be tax, investment, or insurance")
        path = corpus_root / CORPUS_FILES[selected_domain]
        try:
            content = path.read_bytes()
            text = content.decode("utf-8-sig")
        except (OSError, UnicodeError):
            raise ValueError(f"{path.name} is missing or is not valid UTF-8 text") from None

        _validate_section_provenance(text, selected_domain, path.name)

        source_url = None
        retrieved_on = None
        for line in text.splitlines():
            source_match = SOURCE_LINE.fullmatch(line.strip())
            date_match = DATE_LINE.fullmatch(line.strip())
            if source_match and source_url is None:
                source_url = source_match.group(1)
            if date_match and retrieved_on is None:
                try:
                    retrieved_on = date.fromisoformat(date_match.group(1))
                except ValueError:
                    raise ValueError(f"{path.name} has an invalid Accessed date") from None
            if source_url is not None and retrieved_on is not None:
                break

        if source_url is None or not _official_source(selected_domain, source_url):
            raise ValueError(f"{path.name} has no valid matching official HTTPS Source URL")
        if retrieved_on is None:
            raise ValueError(f"{path.name} has no valid Accessed date")
        word_count = len(text.split())
        if word_count < 1_500:
            raise ValueError(f"{path.name} has only {word_count} words; at least 1,500 are required")
        sources.append(
            CorpusSource(
                domain=selected_domain,
                path=path,
                content=content,
                source_url=source_url,
                retrieved_on=retrieved_on,
                word_count=word_count,
            )
        )
    return sources


async def _json_or_error(response: httpx.Response, action: str, domain: str) -> dict:
    if not response.is_success:
        raise RuntimeError(f"{action} failed for {domain} (HTTP {response.status_code})")
    try:
        value = response.json()
    except ValueError:
        raise RuntimeError(f"{action} returned malformed JSON for {domain}") from None
    if not isinstance(value, dict):
        raise RuntimeError(f"{action} returned an invalid response for {domain}")
    return value


async def register_and_activate(
    client: httpx.AsyncClient,
    sources: list[CorpusSource],
    *,
    poll_interval_seconds: float = 3,
    max_polls: int = 20,
) -> list[tuple[str, str]]:
    """Upload each source, wait for provider READY, then activate that new ID."""

    completed: list[tuple[str, str]] = []
    for source in sources:
        upload = await client.post(
            "/api/documents",
            data={
                "domain": source.domain,
                "source_url": source.source_url,
                "retrieved_on": source.retrieved_on.isoformat(),
                "document_name": source.path.stem.replace("_", " ").title(),
            },
            files={"file": (source.path.name, source.content, "text/plain")},
        )
        registered = await _json_or_error(upload, "Upload", source.domain)
        document_id = registered.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            raise RuntimeError(f"Upload response omitted the document ID for {source.domain}")

        ready = False
        for attempt in range(max_polls):
            response = await client.get(
                f"/api/documents/{source.domain}/{document_id}/status"
            )
            status_body = await _json_or_error(response, "Status check", source.domain)
            provider_status = status_body.get("provider_status")
            if provider_status == "READY":
                ready = True
                break
            if provider_status == "FAILED":
                raise RuntimeError(f"Nugen marked the {source.domain} document FAILED")
            if provider_status != "PROCESSING":
                raise RuntimeError(f"Status response was invalid for {source.domain}")
            if attempt + 1 < max_polls and poll_interval_seconds:
                await asyncio.sleep(poll_interval_seconds)
        if not ready:
            raise RuntimeError(f"Timed out waiting for the {source.domain} document to become READY")

        activation = await client.put(
            f"/api/documents/{source.domain}/active-source",
            json={"document_ids": [document_id]},
        )
        selection = await _json_or_error(activation, "Source activation", source.domain)
        if selection.get("domain") != source.domain or selection.get("active_document_ids") != [document_id]:
            raise RuntimeError(f"Activation response did not confirm the new {source.domain} source")
        completed.append((source.domain, document_id))
    return completed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", choices=tuple(CORPUS_FILES), help="Limit operation to one domain")
    parser.add_argument("--confirm", action="store_true", help="Upload documents and activate the new source sets")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Local FinPilot API URL")
    parser.add_argument("--poll-interval", type=float, default=3.0)
    parser.add_argument("--max-polls", type=int, default=20)
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    try:
        sources = load_corpus_sources(domain=args.domain)
    except ValueError as exc:
        print(f"Corpus validation failed: {exc}", file=sys.stderr)
        return 2

    print("Corpus registration plan:")
    for source in sources:
        print(
            f"  {source.domain}: {source.path.name}; {source.word_count} words; "
            f"{len(source.content)} bytes; source={source.source_url}; date={source.retrieved_on}"
        )
    if not args.confirm:
        print("Dry run only; no API requests were sent. Review this plan, then rerun with --confirm.")
        return 0
    if args.max_polls < 1 or args.poll_interval < 0:
        print("--max-polls must be positive and --poll-interval cannot be negative.", file=sys.stderr)
        return 2

    try:
        async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=30.0) as client:
            completed = await register_and_activate(
                client,
                sources,
                poll_interval_seconds=args.poll_interval,
                max_polls=args.max_polls,
            )
    except (httpx.HTTPError, RuntimeError) as exc:
        print(f"Registration stopped safely: {exc}", file=sys.stderr)
        return 1
    for domain, document_id in completed:
        print(f"Activated {domain} source document {document_id}.")
    return 0


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
