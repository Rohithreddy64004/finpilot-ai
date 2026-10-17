"""Run the fixed router/system benchmark; provider calls are explicit CLI opt-ins."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from backend.app.config import get_settings
from backend.app.inference_api import _find_base_model
from backend.app.nugen.client import NugenClient
from backend.app.nugen.errors import NugenConfigurationError
from backend.app.nugen.schemas import ChatCompletionRequest, NugenMessage
from backend.app.router import route


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "eval" / "questions.json"
RESULTS_PATH = ROOT / "eval"


def load_dataset() -> list[dict[str, Any]]:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if not isinstance(dataset, list) or len(dataset) < 30:
        raise ValueError("eval/questions.json must contain at least 30 test cases")
    ids = [item.get("id") for item in dataset]
    if len(set(ids)) != len(ids) or any(not item for item in ids):
        raise ValueError("Evaluation case IDs must be non-empty and unique")
    for item in dataset:
        expected = item.get("expected_experts")
        if not isinstance(expected, list) or not isinstance(item.get("question"), str):
            raise ValueError("Each evaluation case needs a question and expected_experts list")
        must_mention = item.get("must_mention", [])
        if not isinstance(must_mention, list) or not all(
            isinstance(term, str) and term.strip() for term in must_mention
        ):
            raise ValueError("must_mention must contain non-empty strings")
    return dataset


def exact_set_accuracy(expected: list[str], actual: list[str]) -> bool:
    return set(expected) == set(actual)


async def direct_baseline(client: NugenClient, model_id: str, question: str) -> str:
    response = await client.chat_completions(
        ChatCompletionRequest(
            model=model_id,
            messages=[
                NugenMessage(
                    role="system",
                    content=(
                        "Answer as a general finance education model. No retrieval context is "
                        "provided. State uncertainty and do not invent legal or financial facts."
                    ),
                ),
                NugenMessage(role="user", content=question),
            ],
            max_tokens=350,
            temperature=0.2,
        )
    )
    return response.choices[0].message.content or ""


async def run_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    dataset = load_dataset()
    failures: list[dict[str, str]] = []
    routing_correct = 0
    grounded = 0
    latencies: list[float] = []
    baseline_latencies: list[float] = []
    baseline_answer_count = 0
    baseline_term_total = 0
    baseline_terms_found = 0
    full_term_total = 0
    full_terms_found = 0

    async with NugenClient(settings) as client:
        verified_model_id: str | None = None
        if not args.routing_only:
            if not settings.nugen_test_model_id:
                raise NugenConfigurationError("NUGEN_TEST_MODEL_ID is not configured.")
            verified = await _find_base_model(client, settings.nugen_test_model_id)
            if verified is None:
                raise NugenConfigurationError(
                    "The configured baseline model was not confirmed inference-ready by Nugen."
                )
            verified_model_id = verified.model_id

        for case in dataset:
            expected = case["expected_experts"]
            if args.routing_only:
                decision = await route(case["question"], client=client, settings=settings)
                actual = decision.experts
                if not exact_set_accuracy(expected, actual):
                    failures.append({"id": case["id"], "stage": "routing"})
                routing_correct += exact_set_accuracy(expected, actual)
                continue

            assert verified_model_id is not None
            try:
                baseline_started = time.perf_counter()
                baseline = await direct_baseline(client, verified_model_id, case["question"])
                baseline_answer_count += bool(baseline.strip())
                baseline_latencies.append((time.perf_counter() - baseline_started) * 1000)
                baseline_text = baseline.casefold()
                for term in case.get("must_mention", []):
                    baseline_term_total += 1
                    if term.casefold() in baseline_text:
                        baseline_terms_found += 1
                    else:
                        failures.append({"id": case["id"], "stage": f"baseline_missing:{term}"})
            except Exception:
                failures.append({"id": case["id"], "stage": "baseline"})

            try:
                async with httpx.AsyncClient(
                    base_url=args.api_url.rstrip("/"), timeout=args.request_timeout
                ) as api:
                    response = await api.post("/api/ask", json={"question": case["question"]})
                response.raise_for_status()
                result = response.json()
                actual = result.get("routing", {}).get("experts", [])
                case_correct = exact_set_accuracy(expected, actual)
                routing_correct += case_correct
                if not case_correct:
                    failures.append({"id": case["id"], "stage": "routing"})
                is_grounded = bool(result.get("final_answer_source_grounding_supported"))
                grounded += is_grounded
                latency = result.get("latency_ms", {}).get("total_ms")
                if isinstance(latency, (int, float)):
                    latencies.append(float(latency))
                answer_text = " ".join(
                    [result.get("final_answer", "")]
                    + [expert.get("answer", "") for expert in result.get("experts", [])]
                ).casefold()
                for term in case.get("must_mention", []):
                    full_term_total += 1
                    if term.casefold() not in answer_text:
                        failures.append({"id": case["id"], "stage": f"missing:{term}"})
                    else:
                        full_terms_found += 1
            except Exception:
                failures.append({"id": case["id"], "stage": "full_system"})

    result_payload: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "eval/questions.json",
        "case_count": len(dataset),
        "mode": "routing-only" if args.routing_only else "baseline-and-full-system",
        "routing_accuracy": routing_correct / len(dataset),
        "failure_cases": failures,
    }
    if args.routing_only:
        result_payload["note"] = (
            "Router-only mode skipped domain expert inference and direct baseline answers; "
            "the router may still call the configured Nugen model."
        )
        print(f"Routing accuracy: {routing_correct}/{len(dataset)} ({routing_correct / len(dataset):.1%})")
    else:
        result_payload.update(
            {
                "grounded_answer_percent": grounded / len(dataset) * 100,
                "average_latency_ms": statistics.mean(latencies) if latencies else None,
                "baseline_answers_returned": baseline_answer_count,
                "baseline_must_mention_coverage_percent": (
                    baseline_terms_found / baseline_term_total * 100
                    if baseline_term_total
                    else None
                ),
                "full_system_must_mention_coverage_percent": (
                    full_terms_found / full_term_total * 100
                    if full_term_total
                    else None
                ),
                "baseline_average_latency_ms": (
                    statistics.mean(baseline_latencies) if baseline_latencies else None
                ),
                "baseline_description": "One verified base-model completion per case without retrieval.",
                "full_system_description": (
                    "POST /api/ask with routing, domain retrieval, expert calls, and synthesis."
                ),
            }
        )
        print("Metric                  Baseline                 Full system")
        print(f"Cases                   {len(dataset):<24} {len(dataset)}")
        print(f"Routing accuracy        not applicable           {routing_correct / len(dataset):.1%}")
        print(f"Grounded answer rate    0% (no retrieval)        {grounded / len(dataset):.1%}")
        print(
            "Required-term coverage "
            f"{result_payload['baseline_must_mention_coverage_percent']}% "
            f"                 {result_payload['full_system_must_mention_coverage_percent']}%"
        )
        print(
            "Average latency         "
            f"{result_payload['baseline_average_latency_ms']} ms"
            f"              {result_payload['average_latency_ms']} ms"
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = RESULTS_PATH / f"results_{timestamp}.json"
    output_path.write_text(json.dumps(result_payload, indent=2), encoding="utf-8")
    print(f"Failure cases: {len(failures)}")
    print(f"Results saved to {output_path.relative_to(ROOT)}")
    return result_payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--routing-only",
        action="store_true",
        help="Only evaluate routing; skip expert calls, baseline answers, and retrieval.",
    )
    parser.add_argument(
        "--confirm-full-eval",
        action="store_true",
        help="Explicitly allow the baseline and full-system inference calls in full evaluation mode.",
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--request-timeout", type=float, default=120.0)
    args = parser.parse_args(argv)
    if args.request_timeout <= 0:
        parser.error("--request-timeout must be positive")
    if not args.routing_only and not args.confirm_full_eval:
        parser.error(
            "Full evaluation makes base-model and expert calls; pass --confirm-full-eval or use --routing-only."
        )
    return args


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run_evaluation(args))
    except (NugenConfigurationError, ValueError, httpx.HTTPError) as exc:
        print(f"Evaluation could not run: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Avoid printing arbitrary exceptions that could contain request data.
        print(f"Evaluation failed safely ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
