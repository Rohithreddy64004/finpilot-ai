"""Check committed knowledge text for provenance, length, and prompt leakage."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORD = re.compile(r"[\w₹]+(?:[’'-][\w]+)*", re.UNICODE)
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


def corpus_files(root: Path = ROOT / "knowledge_bases") -> list[Path]:
    return sorted(
        set(root.glob("*_expert.txt"))
        | set((root / "prepared").glob("*.txt"))
    )


def inspect_file(path: Path) -> tuple[int, list[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return 0, ["not readable as UTF-8"]
    words = len(WORD.findall(text))
    issues: list[str] = []
    header = "\n".join(text.splitlines()[:30])
    if not SOURCE_HEADER.search(header):
        issues.append("missing source header")
    if not DATE_HEADER.search(header):
        issues.append("missing access or retrieval date")
    if words < 500:
        issues.append(f"under 500 words ({words})")
    if INSTRUCTION_TEXT.search(text):
        issues.append("contains assistant instruction or document-maintenance text")
    return words, issues


def main() -> int:
    files = corpus_files()
    if not files:
        print("No committed knowledge-base text files were found.", file=sys.stderr)
        return 2
    failed = False
    for path in files:
        words, issues = inspect_file(path)
        label = path.relative_to(ROOT)
        if issues:
            failed = True
            print(f"FAIL {label}: {words} words; " + "; ".join(issues))
        else:
            print(f"PASS {label}: {words} words")
    if failed:
        print("Corpus check failed. Review flagged files before packaging; their contents were not changed.")
        return 1
    print(f"Corpus check passed for {len(files)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
