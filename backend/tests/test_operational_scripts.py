import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from backend.app.schemas import AlignmentJobRecord
from scripts.alignment_report import refreshed_domain_report
from scripts.register_corpus import CorpusSource, load_corpus_sources, register_and_activate
from scripts.smoke_test import CASES, validate_smoke_response

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "script_name",
    ["alignment_report.py", "run_eval.py", "seed_content.py"],
)
def test_operational_script_help_is_safe_and_runs_from_outside_repo_root(script_name: str) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script_name), "--help"],
        cwd=ROOT / "scripts",
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.casefold()


def _source(tmp_path: Path, domain: str = "tax") -> CorpusSource:
    path = tmp_path / f"{domain}_expert.txt"
    content = b"mocked source document"
    return CorpusSource(
        domain=domain,
        path=path,
        content=content,
        source_url="https://www.incometax.gov.in/official",
        retrieved_on=date(2026, 10, 17),
        word_count=600,
    )


def test_corpus_registration_plan_validates_metadata_without_provider_calls(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    text = (
        "1. TAX SOURCE SUMMARY\n"
        "Source: https://www.incometax.gov.in/official\n"
        "Accessed: 2026-10-17\n"
        "Source title: Official tax source\n"
        "Publisher: Income Tax Department\n"
        + "educational tax information " * 550
    )
    (corpus / "tax_expert.txt").write_text(text, encoding="utf-8")

    sources = load_corpus_sources(domain="tax", corpus_root=corpus)

    assert len(sources) == 1
    assert sources[0].domain == "tax"
    assert sources[0].retrieved_on == date(2026, 10, 17)
    assert sources[0].word_count >= 500


def test_corpus_registration_rejects_invalid_provenance_in_later_section(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    text = (
        "1. TAX SOURCE SUMMARY\n"
        "Source: https://www.incometax.gov.in/official\n"
        "Accessed: 2026-10-17\n"
        "Source title: Official tax source\n"
        "Publisher: Income Tax Department\n"
        "Tax education.\n"
        "2. TAX CONDITIONS\n"
        "Source: https://www.incometax.gov.in/page?redirect=unsafe\n"
        "Accessed: 2026-10-17\n"
        "Source title: Official tax source\n"
        "Publisher: Income Tax Department\n"
        + "educational tax information " * 550
    )
    (corpus / "tax_expert.txt").write_text(text, encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=r"section 2\. TAX CONDITIONS has an invalid official source URL",
    ):
        load_corpus_sources(domain="tax", corpus_root=corpus)


@pytest.mark.asyncio
async def test_register_uploads_polls_ready_then_activates_only_the_new_id(tmp_path: Path) -> None:
    events: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        events.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            assert b"mocked source document" in await request.aread()
            return httpx.Response(200, json={"document_id": "doc-new"})
        if request.method == "GET":
            return httpx.Response(200, json={"provider_status": "READY"})
        assert request.method == "PUT"
        assert (await request.aread()) == b'{"document_ids":["doc-new"]}'
        return httpx.Response(
            200,
            json={"domain": "tax", "active_document_ids": ["doc-new"], "revision": 2},
        )

    async with httpx.AsyncClient(
        base_url="http://finpilot.test", transport=httpx.MockTransport(handler)
    ) as client:
        result = await register_and_activate(
            client, [_source(tmp_path)], poll_interval_seconds=0, max_polls=1
        )

    assert result == [("tax", "doc-new")]
    assert [event.split(" ", 1)[0] for event in events] == ["POST", "GET", "PUT"]


@pytest.mark.asyncio
async def test_register_stops_on_failed_document_without_activating(tmp_path: Path) -> None:
    events: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        events.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"document_id": "doc-failed"})
        return httpx.Response(200, json={"provider_status": "FAILED"})

    async with httpx.AsyncClient(
        base_url="http://finpilot.test", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(RuntimeError, match="marked the tax document FAILED"):
            await register_and_activate(client, [_source(tmp_path)], max_polls=1)

    assert events == ["POST", "GET"]


def test_smoke_response_requires_matching_routes_and_real_source_citations() -> None:
    case = CASES[3]
    good = {
        "routing": {"experts": ["tax", "investment"]},
        "experts": [
            {"status": "complete", "evidence": [{"citation_id": "S1"}]},
            {"status": "complete", "evidence": [{"citation_id": "S2"}]},
        ],
        "sources": [{"citation_id": "S1"}, {"citation_id": "S2"}],
        "final_answer": "Read the scheme terms [S1] and risk information [S2].",
    }
    assert validate_smoke_response(case, good) == (
        True,
        "routing, expert results, and citation references are present",
    )

    bad = {**good, "final_answer": "An unsupported citation [S9]."}
    passed, note = validate_smoke_response(case, bad)
    assert passed is False
    assert "do not map" in note


class FakeAlignmentRepository:
    def __init__(self, records: list[AlignmentJobRecord] | None = None) -> None:
        self.records = records or []
        self.saved: list[AlignmentJobRecord] = []

    async def list_by_domain(self, domain: str, *, limit: int = 100):
        return [record for record in self.records if record.domain == domain][:limit]

    async def save(self, record: AlignmentJobRecord) -> None:
        self.saved.append(record)

    async def get_by_tracking_id(self, tracking_id: str):
        return next((row for row in self.records if row.tracking_id == tracking_id), None)


class NoCallAlignmentService:
    async def alignment_status(self, _alignment_id: str):
        raise AssertionError("No provider status should be queried without a tracked alignment ID")


@pytest.mark.asyncio
async def test_alignment_report_labels_missing_jobs_unaligned_and_unverified() -> None:
    report = await refreshed_domain_report(FakeAlignmentRepository(), NoCallAlignmentService())

    assert [row["domain"] for row in report] == ["tax", "investment", "insurance"]
    assert all(row["alignment_status"] == "unaligned" for row in report)
    assert all(row["verification_state"] == "UNVERIFIED" for row in report)
    assert all(row["inference_ready"] is False for row in report)


def make_alignment_job() -> AlignmentJobRecord:
    now = datetime.now(timezone.utc)
    return AlignmentJobRecord(
        tracking_id="tracking-tax",
        domain="tax",
        alignment_name="Tax education",
        base_model_id="base-model",
        document_ids=["document-tax"],
        alignment_id=None,
        submission_state="SUBMITTED",
        alignment_status="unaligned",
        verification_state="UNVERIFIED",
        inference_ready=False,
        status_note="Nugen submission is not verified.",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_alignment_report_does_not_promote_submission_without_provider_model() -> None:
    report = await refreshed_domain_report(
        FakeAlignmentRepository([make_alignment_job()]), NoCallAlignmentService()
    )

    tax = report[0]
    assert tax["alignment_status"] == "unaligned"
    assert tax["verification_state"] == "UNVERIFIED"
    assert tax["model_id"] is None
    assert tax["inference_ready"] is False
