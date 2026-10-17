from pathlib import Path

from scripts.check_corpus import corpus_files, inspect_file


def test_corpus_scanner_covers_active_and_prepared_source_text() -> None:
    files = corpus_files(Path(__file__).resolve().parents[2] / "knowledge_bases")
    relative = {str(path).replace("\\", "/") for path in files}
    assert any(path.endswith("/tax_expert.txt") for path in relative)
    assert any("/prepared/" in path for path in relative)


def test_corpus_check_flags_short_text_missing_provenance_and_instructions(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.txt"
    path.write_text(
        "Source: https://example.gov.in\nAccessed: 2026-10-17\n"
        "How FinPilot should answer this question.\nshort text",
        encoding="utf-8",
    )
    words, issues = inspect_file(path)
    assert words < 500
    assert "under 500 words" in " ".join(issues)
    assert "contains assistant instruction or document-maintenance text" in issues
