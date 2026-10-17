"""Manually smoke-test the local FinPilot API without printing answer content."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class SmokeCase:
    name: str
    question: str
    experts: list[str] | None
    expected_experts: set[str]
    expects_evidence: bool


CASES = (
    SmokeCase(
        "tax slabs",
        "How do income tax slabs work for individuals under 60 in AY 2026-27?",
        ["tax"],
        {"tax"},
        True,
    ),
    SmokeCase(
        "investment education",
        "What is the difference between saving and investing?",
        ["investment"],
        {"investment"},
        True,
    ),
    SmokeCase(
        "insurance education",
        "What does a waiting period mean in health insurance?",
        ["insurance"],
        {"insurance"},
        True,
    ),
    SmokeCase(
        "ELSS and NPS cross-domain",
        "Compare ELSS and NPS for tax education and investment risk; do not recommend one.",
        None,
        {"tax", "investment"},
        True,
    ),
    SmokeCase(
        "out of scope",
        "What is the weather forecast in Hyderabad?",
        None,
        set(),
        False,
    ),
)
_CITATION = re.compile(r"\[(S[1-9][0-9]*)\]")


def validate_smoke_response(case: SmokeCase, payload: object) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "response is not an object"
    routing = payload.get("routing")
    if not isinstance(routing, dict) or not isinstance(routing.get("experts"), list):
        return False, "routing data is missing"
    selected = set(routing["experts"])
    if selected != case.expected_experts:
        return False, f"expected experts {sorted(case.expected_experts)}, got {sorted(selected)}"
    experts = payload.get("experts")
    sources = payload.get("sources")
    answer = payload.get("final_answer")
    if not isinstance(experts, list) or not isinstance(sources, list) or not isinstance(answer, str):
        return False, "answer or source data is missing"
    if not case.expects_evidence:
        return (not experts, "out-of-scope response has no expert results" if not experts else "experts were invoked")
    if not experts or any(not isinstance(item, dict) or item.get("status") != "complete" for item in experts):
        return False, "one or more selected experts did not complete"
    source_ids = {item.get("citation_id") for item in sources if isinstance(item, dict)}
    markers = set(_CITATION.findall(answer))
    if not sources or not markers or not markers.issubset(source_ids):
        return False, "final answer citations do not map to returned sources"
    if any(not isinstance(item.get("evidence"), list) or not item["evidence"] for item in experts):
        return False, "one or more experts returned no evidence passages"
    return True, "routing, expert results, and citation references are present"


async def run_smoke_tests(client: httpx.AsyncClient) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    for case in CASES:
        body = {"question": case.question}
        if case.experts is not None:
            body["experts"] = case.experts
        try:
            response = await client.post("/api/ask", json=body)
            if response.status_code != 200:
                results.append((case.name, False, f"HTTP {response.status_code}"))
                continue
            passed, detail = validate_smoke_response(case, response.json())
            results.append((case.name, passed, detail))
        except (httpx.HTTPError, ValueError):
            results.append((case.name, False, "request failed or response JSON was invalid"))
    return results


async def main_async(base_url: str) -> int:
    try:
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=90.0) as client:
            health = await client.get("/health")
            if health.status_code != 200:
                print(f"FAIL health: HTTP {health.status_code}")
                return 1
            print("PASS health")
            results = await run_smoke_tests(client)
    except httpx.HTTPError:
        print("FAIL local API connection; start the FinPilot service and retry.")
        return 1
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")
    return 0 if all(result[1] for result in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    return asyncio.run(main_async(args.base_url))


if __name__ == "__main__":
    raise SystemExit(main())
