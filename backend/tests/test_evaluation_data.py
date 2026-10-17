from pathlib import Path

import pytest

from scripts.run_eval import exact_set_accuracy, load_dataset, parse_args


ROOT = Path(__file__).resolve().parents[2]


def test_fixed_eval_dataset_has_thirty_cases_and_domain_coverage() -> None:
    dataset = load_dataset()
    assert len(dataset) >= 30
    expected_sets = [set(item["expected_experts"]) for item in dataset]
    assert {"tax"} in expected_sets
    assert {"investment"} in expected_sets
    assert {"insurance"} in expected_sets
    assert {"tax", "investment"} in expected_sets
    assert set() in expected_sets


def test_must_mention_terms_are_present_in_committed_domain_text() -> None:
    dataset = load_dataset()
    domain_files = {
        "tax": ROOT / "knowledge_bases" / "tax_expert.txt",
        "investment": ROOT / "knowledge_bases" / "investment_expert.txt",
        "insurance": ROOT / "knowledge_bases" / "insurance_expert.txt",
    }
    source_text = {
        domain: path.read_text(encoding="utf-8").casefold()
        for domain, path in domain_files.items()
    }
    for case in dataset:
        for term in case.get("must_mention", []):
            assert any(term.casefold() in source_text[domain] for domain in case["expected_experts"]), (
                case["id"],
                term,
            )


def test_eval_exact_set_comparison_ignores_order_but_not_missing_domains() -> None:
    assert exact_set_accuracy(["tax", "investment"], ["investment", "tax"])
    assert not exact_set_accuracy(["tax", "investment"], ["tax"])


def test_full_eval_requires_explicit_confirmation_but_routing_only_is_available() -> None:
    assert parse_args(["--routing-only"]).routing_only is True
    assert parse_args(["--confirm-full-eval"]).confirm_full_eval is True
    with pytest.raises(SystemExit):
        parse_args([])
