"""Nugen-assisted finance question routing with a deterministic fallback."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from backend.app.config import Settings, get_settings
from backend.app.inference_api import _find_base_model
from backend.app.nugen.client import NugenClient
from backend.app.nugen.errors import NugenAPIError, NugenConfigurationError
from backend.app.nugen.schemas import ChatCompletionRequest, NugenMessage
from backend.app.schemas import ExpertName


_EXAMPLES_PATH = Path(__file__).resolve().parents[2] / "eval" / "router_examples.json"
_EXPERT_ORDER: tuple[ExpertName, ...] = ("tax", "investment", "insurance")


class RouterDecision(BaseModel):
    """Validated routing result; an empty expert list means out of scope."""

    model_config = ConfigDict(extra="forbid")

    experts: list[ExpertName] = Field(max_length=3)
    reason: str = Field(min_length=1, max_length=500)
    routing_method: Literal["model", "fallback"]

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must not be blank")
        return value

    @field_validator("experts")
    @classmethod
    def experts_must_be_unique(cls, value: list[ExpertName]) -> list[ExpertName]:
        if len(set(value)) != len(value):
            raise ValueError("experts must not contain duplicates")
        return value

    @model_validator(mode="after")
    def out_of_scope_reason_required(self) -> "RouterDecision":
        if not self.experts and "out of scope" not in self.reason.casefold():
            raise ValueError("empty expert list requires a clear out-of-scope reason")
        return self


class _RouterExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    experts: list[ExpertName] = Field(max_length=3)
    reason: str = Field(min_length=1)


@lru_cache(maxsize=1)
def _load_examples() -> tuple[_RouterExample, ...]:
    payload = json.loads(_EXAMPLES_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("router_examples.json must contain a JSON array")
    examples = tuple(_RouterExample.model_validate(item) for item in payload)
    if len(examples) < 8:
        raise ValueError("router_examples.json must contain at least eight examples")
    if not any(not example.experts for example in examples):
        raise ValueError("router_examples.json must include an out-of-scope example")
    return examples


def _fallback_route(question: str, reason: str) -> RouterDecision:
    text = question.casefold()
    selected = _deterministic_domains(question)
    if not selected:
        return RouterDecision(
            experts=[],
            reason=(
                "Out of scope: this question does not match Indian tax, investment, or "
                "insurance education. No expert was called."
            ),
            routing_method="fallback",
        )
    chosen = ", ".join(selected)
    return RouterDecision(
        experts=selected,
        reason=f"Keyword fallback selected {chosen} based on finance-domain terms. {reason}",
        routing_method="fallback",
    )


def _deterministic_domains(question: str) -> list[ExpertName]:
    """Add expert domains implied by explicit products and concepts."""
    text = question.casefold()
    keyword_groups: dict[ExpertName, tuple[str, ...]] = {
        "tax": (
            "income tax", "tax", "tds", "itr", "assessment year", "financial year",
            "taxable income", "deduction", "rebate", "gst", "capital gains", "80c", "80ccd",
            "regime", "slab",
        ),
        "investment": (
            "invest", "mutual fund", "sip", "stock", "share", "sebi", "diversification",
            "portfolio", "return", "liquidity", "risk",
        ),
        "insurance": (
            "insurance", "insurer", "policy", "premium", "claim", "irdai", "coverage",
            "cover", "deductible", "waiting period", "sum insured",
        ),
    }
    explicit_products = {
        "investment": (r"\belss\b", r"\bnps\b", r"\bppf\b", r"\bsips?\b", r"\betfs?\b", r"\bmutual\s+funds?\b"),
        "tax": (r"\b80c\b", r"\b80ccd\b", r"\bregime\b", r"\bslabs?\b", r"\bitrs?\b", r"\brebate\b", r"\bdeductions?\b", r"\btax[- ]saving\b"),
        "insurance": (r"\bpremiums?\b", r"\bclaims?\b", r"\bwaiting\s+period\b", r"\bpolicies\b|\bpolicy\b", r"\bcovers?\b"),
    }
    selected: list[ExpertName] = []
    for expert in _EXPERT_ORDER:
        if any(keyword in text for keyword in keyword_groups[expert]) or any(
            re.search(pattern, text) for pattern in explicit_products[expert]
        ):
            selected.append(expert)
    return selected


def _apply_deterministic_domains(decision: RouterDecision, question: str) -> RouterDecision:
    required = _deterministic_domains(question)
    if not required:
        return decision
    merged = [expert for expert in _EXPERT_ORDER if expert in decision.experts or expert in required]
    added = [expert for expert in required if expert not in decision.experts]
    if not added:
        return decision
    reason = decision.reason
    if not decision.experts:
        reason = "Deterministic finance terms selected the relevant expert domains."
    reason += f" Deterministic term matching added: {', '.join(added)}."
    return RouterDecision(
        experts=merged,
        reason=reason,
        routing_method="fallback" if not decision.experts else decision.routing_method,
    )


def _router_prompt() -> str:
    examples = _load_examples()
    few_shot = "\n\n".join(
        f"Question: {example.question}\n"
        f"JSON: {json.dumps({'experts': example.experts, 'reason': example.reason})}"
        for example in examples
    )
    return (
        "Route a user's question to the relevant FinPilot education experts. Return exactly one "
        'JSON object with keys "experts" and "reason"; no Markdown or other text. '
        "Experts may only be tax, investment, or insurance, with no duplicates. Use one to three "
        "experts for an in-scope question. Use an empty experts list only for out-of-scope questions, "
        "and make the reason clearly say 'Out of scope'. A cross-domain question may select multiple "
        "experts. Do not answer the question.\n\nFew-shot examples:\n"
        f"{few_shot}"
    )


def _parse_model_route(content: str) -> RouterDecision:
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("router response must be a JSON object")
    decision = RouterDecision.model_validate(
        {**parsed, "routing_method": "model"}
    )
    return decision


async def _route_with_client(
    question: str,
    client: NugenClient,
    settings: Settings,
) -> RouterDecision:
    model_id = (settings.nugen_test_model_id or "").strip()
    if not model_id:
        return _fallback_route(question, "The configured routing model is missing.")

    try:
        verified_model = await _find_base_model(client, model_id)
        if verified_model is None:
            return _fallback_route(
                question,
                "The configured routing model was not confirmed inference-ready by Nugen.",
            )
        completion = await client.chat_completions(
            ChatCompletionRequest(
                model=verified_model.model_id,
                messages=[
                    NugenMessage(role="system", content=_router_prompt()),
                    NugenMessage(role="user", content=question),
                ],
                max_tokens=250,
                prompt_truncate_len=2200,
                temperature=0,
                stream=False,
                n=1,
            )
        )
        content = completion.choices[0].message.content if completion.choices else None
        if not content:
            raise ValueError("router completion contained no assistant content")
        return _apply_deterministic_domains(_parse_model_route(content), question)
    except (NugenAPIError, NugenConfigurationError, ValidationError, ValueError, TypeError):
        return _fallback_route(
            question,
            "Model routing failed validation or was unavailable; keyword fallback was used.",
        )


async def route(
    question: str,
    *,
    client: NugenClient | None = None,
    settings: Settings | None = None,
) -> RouterDecision:
    """Use the verified configured base model once, then fail closed to keywords."""

    runtime_settings = settings or get_settings()
    managed_client = client or NugenClient(runtime_settings)
    try:
        return await _route_with_client(question, managed_client, runtime_settings)
    finally:
        if client is None:
            await managed_client.aclose()
