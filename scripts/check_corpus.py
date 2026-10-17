"""Check committed knowledge text for provenance, length, and prompt leakage."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.corpus_quality import inspect_corpus_text


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
    return inspect_corpus_text(path.name, text)


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
