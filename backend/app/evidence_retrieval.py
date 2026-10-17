"""Small local BM25 retriever over the existing, domain-separated text files."""

from __future__ import annotations

import asyncio
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

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
_HEADING_PATTERN = re.compile(
    r"(?m)^\s*\d+\.\s+[A-Z][A-Z0-9 /&(),'-]*(?=\s+[A-Z][a-z])"
)
_CITATION_REFERENCE = re.compile(r"\[(S\d+)\]")
_TAX_SLAB_QUERY = re.compile(
    r"\b(?:tax\s+)?slabs?\b|\b(?:tax\s+)?brackets?\b|\bslab\s+rates?\b|\btax\s+rates?\b",
    re.IGNORECASE,
)
_TAX_SLAB_TERMS = re.compile(
    r"\b(?:slabs?|brackets?|thresholds?|income range|taxable income)\b",
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
    return [
        token.casefold()
        for token in _TOKEN_PATTERN.findall(text)
        if len(token) > 1 and token.casefold() not in _STOP_WORDS
    ]


def _sections(text: str) -> list[str]:
    """Return numbered knowledge sections, omitting the title/source preamble."""

    matches = list(_HEADING_PATTERN.finditer(text))
    if not matches:
        return []
    starts = [match.start() for match in matches]
    sections: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        section = re.sub(r"\s+", " ", text[start:end]).strip()
        if section:
            sections.append(section)
    return sections


def _split_passages(text: str, *, max_chars: int = 1_100) -> list[str]:
    passages: list[str] = []
    for section in _sections(text):
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
    candidates: list[tuple[SourceDocumentRecord, int, str]],
) -> list[tuple[float, tuple[SourceDocumentRecord, int, str]]]:
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

    ranked: list[tuple[float, tuple[SourceDocumentRecord, int, str]]] = []
    for index, candidate in enumerate(candidates):
        score = base_scores[index]
        if slab_query:
            # Keep original query relevance while letting table vocabulary help
            # when the user's wording is broad (for example, "how slabs work").
            score += 0.35 * expanded_scores[index] * base_max / expanded_max
            excerpt = candidate[2]
            if _contains_tax_slab_details(excerpt):
                score += max(base_max, 1.0) * 2.0
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
            "The selected source document belongs to a different expert domain."
        )

    content = await content_store.get(domain, record.document_id)
    if content is None:
        if record.original_filename != EXPECTED_CORPUS_FILES[domain]:
            raise SourceContentUnavailableError(
                f"Registered {domain} document text is not available in the local corpus."
            )
        root = corpus_root or _ROOT
        file_path = root / EXPECTED_CORPUS_FILES[domain]
        try:
            content = await asyncio.to_thread(file_path.read_bytes)
        except OSError:
            raise SourceContentUnavailableError(
                f"Registered {domain} document text is not available in the local corpus."
            ) from None

    if len(content) != record.size_bytes:
        raise SourceContentUnavailableError(
            f"Local {domain} document size does not match its registered metadata."
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SourceContentUnavailableError(
            f"The local {domain} document is not valid UTF-8 text."
        ) from None
    if not text.strip():
        raise SourceContentUnavailableError(
            f"The local {domain} document has no readable text."
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
    candidates: list[tuple[SourceDocumentRecord, int, str]] = []
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

        for passage_index, excerpt in enumerate(_split_passages(text), start=1):
            if _tokens(excerpt):
                candidates.append((record, passage_index, excerpt))

    ranked = _rank_passages(domain, question, candidates)

    selected: list[RetrievedPassage] = []
    used_characters = 0
    for score, (record, passage_index, excerpt) in ranked:
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
                source=SourceReference(
                    title=record.document_name,
                    publisher=DOMAIN_PUBLISHERS[domain],
                    url=record.source_url,
                    accessed_on=record.retrieved_on,
                ),
                score=score,
            )
        )
        used_characters += len(excerpt)
    return selected


def citation_reference_ids(text: str) -> set[str]:
    """Return citation markers exactly matching the prompt's [S1] form."""

    return set(_CITATION_REFERENCE.findall(text))
