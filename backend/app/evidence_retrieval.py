"""Small local BM25 retriever over the existing, domain-separated text files."""

from __future__ import annotations

import asyncio
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from backend.app.schemas import ExpertName, SourceDocumentRecord, SourceReference
from backend.app.source_content_store import LocalSourceContentStore


class SourceContentUnavailableError(RuntimeError):
    """A registered document has no safely matched local text copy."""


EXPECTED_CORPUS_FILES: dict[ExpertName, str] = {
    "tax": "tax_expert.txt",
    "investment": "investment_expert.txt",
    "insurance": "insurance_expert.txt",
}
DOMAIN_PUBLISHERS: dict[ExpertName, str] = {
    "tax": "Income Tax Department, Government of India",
    "investment": "Securities and Exchange Board of India",
    "insurance": "Insurance Regulatory and Development Authority of India",
}

_ROOT = Path(__file__).resolve().parents[2] / "knowledge_bases"
_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_HEADING_PATTERN = re.compile(r"(?m)^\s*\d+\.\s+[^\r\n]+$")
_INSTRUCTION_HEADING = re.compile(
    r"\b(?:HOW\s+FINPILOT\s+SHOULD|DOCUMENT\s+MAINTENANCE|"
    r"ASSISTANT\s+INSTRUCTIONS|YOU\s+ARE\s+FINPILOT\s+AI)\b",
    re.IGNORECASE,
)
_CITATION_REFERENCE = re.compile(r"\[(S\d+)\]")
_SOURCE_FIELDS = {
    "source": re.compile(r"^Source:\s*(\S+)\s*$", re.IGNORECASE),
    "accessed": re.compile(r"^Accessed:\s*(\d{4}-\d{2}-\d{2})\s*$", re.IGNORECASE),
    "title": re.compile(r"^Source title:\s*(.+?)\s*$", re.IGNORECASE),
    "publisher": re.compile(r"^Publisher:\s*(.+?)\s*$", re.IGNORECASE),
}
_SOURCE_FIELD_NAMES = {
    "Source", "Accessed", "Source title", "Publisher",
}
_OFFICIAL_SOURCE_HOSTS: dict[ExpertName, tuple[str, ...]] = {
    "tax": ("incometax.gov.in", "incometaxindia.gov.in"),
    "investment": ("investor.sebi.gov.in", "sebi.gov.in"),
    "insurance": ("irdai.gov.in",),
}
_TAX_SLAB_QUERY = re.compile(
    r"\b(?:tax\s+)?slabs?\b|\b(?:tax\s+)?brackets?\b|\bslab\s+rates?\b|\btax\s+rates?\b",
    re.IGNORECASE,
)
_TAX_SLAB_TERMS = re.compile(
    r"\b(?:(?:old|new)[ -]regime(?:\s+ordinary)?\s+slabs?|"
    r"(?:tax\s+)?slab\s+tables?|(?:tax\s+)?brackets?)\b",
    re.IGNORECASE,
)
_TAX_REGIME_COMPARISON = re.compile(
    r"\bold regime\b.*\bnew regime\b|\bnew regime\b.*\bold regime\b",
    re.IGNORECASE,
)
_CURRENCY_AMOUNT = re.compile(r"(?:₹|\bRs\.?\s*)\s*\d[\d,]*", re.IGNORECASE)
_PERCENT_RATE = re.compile(r"\b\d+(?:\.\d+)?\s*%")
_TAX_SLAB_QUERY_EXPANSION = (
    "income tax slab rates thresholds taxable income income range percentage "
    "progressive marginal tax old regime new regime assessment year financial year"
)
_STOP_WORDS = {
    "a", "about", "an", "and", "are", "as", "at", "be", "by", "can", "do",
    "does", "for", "from", "how", "i", "in", "is", "it", "me", "of", "on",
    "or", "please", "should", "tell", "that", "the", "their", "them", "this",
    "to", "was", "what", "when", "where", "which", "who", "why", "with",
    "you", "your",
}


@dataclass(frozen=True, slots=True)
class RetrievedPassage:
    """A ranked excerpt tied to the registered Nugen document and provenance."""

    citation_id: str
    passage_id: str
    document_id: str
    document_name: str
    excerpt: str
    source: SourceReference
    score: float


def _tokens(text: str) -> list[str]:
    normalized: list[str] = []
    for raw_token in _TOKEN_PATTERN.findall(text):
        token = raw_token.casefold()
        if len(token) <= 1 or token in _STOP_WORDS:
            continue

        # A deliberately small normalizer handles common English inflections
        # without adding a stemming dependency or conflating unrelated terms.
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 5 and token.endswith("es") and token[:-2].endswith(
            ("s", "x", "z", "ch", "sh")
        ):
            token = token[:-2]
        elif len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]

        if len(token) > 5 and token.endswith("ing"):
            token = token[:-3]
            if token.endswith("v"):
                token += "e"
            elif len(token) > 2 and token[-1] == token[-2] and token[-1] not in "aeiou":
                token = token[:-1]
        elif len(token) > 5 and token.endswith("ed"):
            token = token[:-2]
            if len(token) > 2 and token[-1] == token[-2] and token[-1] not in "aeiou":
                token = token[:-1]

        if len(token) > 7 and token.endswith("ment"):
            token = token[:-4]
        if len(token) > 1:
            normalized.append(token)
    return normalized


def _sections(text: str) -> list[str]:
    """Return numbered knowledge sections, omitting the title/source preamble."""

    matches = list(_HEADING_PATTERN.finditer(text))
    if not matches:
        return []
    starts = [match.start() for match in matches]
    sections: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        section = text[start:end].strip()
        if section:
            sections.append(section)
    return sections


def _is_instruction_heading(section: str) -> bool:
    first_line = section.splitlines()[0].strip() if section.splitlines() else ""
    heading = re.sub(r"^\d+\.\s*", "", first_line)
    return _INSTRUCTION_HEADING.search(heading) is not None


def _section_source_reference(
    section: str,
    domain: ExpertName,
    fallback: SourceReference,
) -> SourceReference:
    """Use section provenance when present; otherwise retain legacy record metadata."""

    fields: dict[str, str] = {}
    for line in section.splitlines():
        stripped = line.strip()
        for field_name, pattern in _SOURCE_FIELDS.items():
            match = pattern.fullmatch(stripped)
            if match:
                fields[field_name] = match.group(1).strip()

    raw_url = fields.get("source")
    if raw_url is None:
        return fallback

    parsed = urlsplit(raw_url)
    allowed_hosts = _OFFICIAL_SOURCE_HOSTS[domain]
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or not any(
            parsed.hostname.lower() == allowed
            or parsed.hostname.lower().endswith("." + allowed)
            for allowed in allowed_hosts
        )
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SourceContentUnavailableError(
            f"Source content unavailable: a {domain} section contains an invalid official source URL."
        )

    raw_date = fields.get("accessed")
    if raw_date is None:
        raise SourceContentUnavailableError(
            f"Source content unavailable: a {domain} section is missing its access date."
        )
    try:
        accessed_on = date.fromisoformat(raw_date)
    except ValueError:
        raise SourceContentUnavailableError(
            f"Source content unavailable: a {domain} section has an invalid access date."
        ) from None

    title = fields.get("title")
    publisher = fields.get("publisher")
    if not title or not publisher:
        raise SourceContentUnavailableError(
            f"Source content unavailable: a {domain} section is missing its source title or publisher."
        )
    return SourceReference(
        title=title,
        publisher=publisher,
        url=raw_url,
        accessed_on=accessed_on,
    )


def _without_source_fields(section: str) -> str:
    kept_lines = []
    for line in section.splitlines():
        stripped = line.strip()
        if any(pattern.fullmatch(stripped) for pattern in _SOURCE_FIELDS.values()):
            continue
        kept_lines.append(line)
    return " ".join(re.sub(r"\s+", " ", line).strip() for line in kept_lines if line.strip())


def _split_section_passages(section: str, *, max_chars: int = 1_100) -> list[str]:
    passages: list[str] = []
    sentences = re.split(r"(?<=[.!?])\s+", section)
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                passages.append(current.strip())
                current = ""
            for offset in range(0, len(sentence), max_chars):
                part = sentence[offset : offset + max_chars].strip()
                if part:
                    passages.append(part)
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_chars:
            passages.append(current.strip())
            current = sentence
        else:
            current = candidate
    if current:
        passages.append(current.strip())
    return passages


def _split_passages(text: str, *, max_chars: int = 1_100) -> list[str]:
    passages: list[str] = []
    for section in _sections(text):
        passages.extend(_split_section_passages(_without_source_fields(section), max_chars=max_chars))
    return passages


def _bm25_scores(query: str, documents: list[str]) -> list[float]:
    query_terms = set(_tokens(query))
    if not query_terms or not documents:
        return [0.0] * len(documents)

    tokenized = [_tokens(document) for document in documents]
    average_length = sum(map(len, tokenized)) / max(len(tokenized), 1)
    document_frequency = Counter(
        term for terms in tokenized for term in set(terms)
    )
    scores: list[float] = []
    for terms in tokenized:
        term_counts = Counter(terms)
        length = len(terms)
        score = 0.0
        for term in query_terms:
            frequency = term_counts[term]
            if frequency == 0:
                continue
            frequency_in_documents = document_frequency[term]
            inverse_frequency = math.log(
                1 + (len(tokenized) - frequency_in_documents + 0.5)
                / (frequency_in_documents + 0.5)
            )
            denominator = frequency + 1.2 * (
                1 - 0.75 + 0.75 * length / max(average_length, 1)
            )
            score += inverse_frequency * frequency * 2.2 / denominator
        scores.append(score)
    return scores


def is_tax_slab_question(domain: ExpertName, question: str) -> bool:
    """Identify requests that need an actual tax-band table, not a generic intro."""

    return domain == "tax" and _TAX_SLAB_QUERY.search(question) is not None


def has_tax_slab_details(passages: list[RetrievedPassage]) -> bool:
    """Require explicit band terminology, multiple thresholds, and multiple rates."""

    return any(_contains_tax_slab_details(passage.excerpt) for passage in passages)


def _contains_tax_slab_details(excerpt: str) -> bool:
    rates = {rate.replace(" ", "") for rate in _PERCENT_RATE.findall(excerpt)}
    return (
        _TAX_SLAB_TERMS.search(excerpt) is not None
        and len(_CURRENCY_AMOUNT.findall(excerpt)) >= 2
        and len(rates) >= 2
    )


def _rank_passages(
    domain: ExpertName,
    question: str,
    candidates: list[tuple[SourceDocumentRecord, int, str, SourceReference]],
) -> list[tuple[float, tuple[SourceDocumentRecord, int, str, SourceReference]]]:
    """Rank lexical matches and promote source passages with direct slab data.

    FinPilot currently has no configured embedding index. For tax-slab queries,
    a small lexical expansion and explicit table-feature boost act as a local
    reranker without fabricating facts or changing other domains' ranking.
    """

    excerpts = [candidate[2] for candidate in candidates]
    base_scores = _bm25_scores(question, excerpts)
    slab_query = is_tax_slab_question(domain, question)
    expanded_scores = (
        _bm25_scores(f"{question} {_TAX_SLAB_QUERY_EXPANSION}", excerpts)
        if slab_query
        else [0.0] * len(candidates)
    )
    base_max = max(base_scores, default=0.0) or 1.0
    expanded_max = max(expanded_scores, default=0.0) or 1.0

    ranked: list[tuple[float, tuple[SourceDocumentRecord, int, str, SourceReference]]] = []
    for index, candidate in enumerate(candidates):
        score = base_scores[index]
        if slab_query:
            # Keep original query relevance while letting table vocabulary help
            # when the user's wording is broad (for example, "how slabs work").
            score += 0.35 * expanded_scores[index] * base_max / expanded_max
            excerpt = candidate[2]
            if _contains_tax_slab_details(excerpt):
                # Explicit rate tables outrank rebate, cess, and general regime
                # passages that repeat "slab" or "rate" vocabulary.
                score += max(base_max, 1.0) * 4.0
            elif _TAX_REGIME_COMPARISON.search(excerpt) and re.search(
                r"\b(?:deductions?|exemptions?|taxable income)\b",
                excerpt,
                re.IGNORECASE,
            ):
                # A factual old/new regime comparison is useful context for a
                # broad slabs question, but stays below passages with actual
                # numerical slab tables.
                score += max(base_max, 1.0) * 1.5
        ranked.append((score, candidate))
    return sorted(
        (item for item in ranked if item[0] > 0),
        key=lambda item: (-item[0], item[1][0].document_id, item[1][1]),
    )


async def load_registered_document_text(
    domain: ExpertName,
    record: SourceDocumentRecord,
    content_store: LocalSourceContentStore,
    *,
    corpus_root: Path | None = None,
) -> str:
    """Load one document's text and fail closed on missing or mismatched bytes."""

    if record.domain != domain:
        raise SourceContentUnavailableError(
            "Source content unavailable: the selected document belongs to a different expert domain."
        )

    content = await content_store.get(domain, record.document_id)
    if content is None:
        if record.original_filename != EXPECTED_CORPUS_FILES[domain]:
            raise SourceContentUnavailableError(
                f"Source content unavailable: registered {domain} document text is not present in the local content store or corpus."
            )
        root = corpus_root or _ROOT
        file_path = root / EXPECTED_CORPUS_FILES[domain]
        try:
            content = await asyncio.to_thread(file_path.read_bytes)
        except OSError:
            raise SourceContentUnavailableError(
                f"Source content unavailable: registered {domain} document text is not present in the local content store or corpus."
            ) from None

    if len(content) != record.size_bytes:
        raise SourceContentUnavailableError(
            f"Source content unavailable: local {domain} document size does not match its registered metadata."
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SourceContentUnavailableError(
            f"Source content unavailable: the local {domain} document is not valid UTF-8 text."
        ) from None
    if not text.strip():
        raise SourceContentUnavailableError(
            f"Source content unavailable: the local {domain} document has no readable text."
        )
    return text


async def retrieve_domain_passages(
    domain: ExpertName,
    question: str,
    documents: list[SourceDocumentRecord],
    content_store: LocalSourceContentStore,
    *,
    top_k: int = 4,
    max_context_chars: int = 6_000,
    corpus_root: Path | None = None,
) -> list[RetrievedPassage]:
    """Load only registered files for one domain and rank their passages locally.

    Legacy content is accepted only when its fixed domain filename and byte
    count exactly match the document metadata. That check binds the repository
    copy to the old upload record without guessing a path from caller input.
    """

    root = corpus_root or _ROOT
    candidates: list[tuple[SourceDocumentRecord, int, str, SourceReference]] = []
    if any(record.domain != domain for record in documents):
        raise SourceContentUnavailableError(
            "The selected source set contains a document from another expert domain."
        )
    matching_documents = list(documents)
    if not matching_documents:
        return []

    for record in matching_documents:
        text = await load_registered_document_text(
            domain,
            record,
            content_store,
            corpus_root=root,
        )

        fallback_reference = SourceReference(
            title=record.document_name,
            publisher=DOMAIN_PUBLISHERS[domain],
            url=record.source_url,
            accessed_on=record.retrieved_on,
        )
        passage_index = 0
        for section in _sections(text):
            if _is_instruction_heading(section):
                continue
            source_reference = _section_source_reference(section, domain, fallback_reference)
            section_passages = _split_section_passages(_without_source_fields(section))
            for excerpt in section_passages:
                if _tokens(excerpt):
                    passage_index += 1
                    candidates.append((record, passage_index, excerpt, source_reference))

    ranked = _rank_passages(domain, question, candidates)

    selected: list[RetrievedPassage] = []
    used_characters = 0
    for score, (record, passage_index, excerpt, source_reference) in ranked:
        if len(selected) >= top_k:
            break
        if selected and used_characters + len(excerpt) > max_context_chars:
            continue
        citation_id = f"S{len(selected) + 1}"
        selected.append(
            RetrievedPassage(
                citation_id=citation_id,
                passage_id=f"{record.document_id}:{passage_index}",
                document_id=record.document_id,
                document_name=record.document_name,
                excerpt=excerpt,
                source=source_reference,
                score=score,
            )
        )
        used_characters += len(excerpt)
    return selected


def citation_reference_ids(text: str) -> set[str]:
    """Return citation markers exactly matching the prompt's [S1] form."""

    return set(_CITATION_REFERENCE.findall(text))
