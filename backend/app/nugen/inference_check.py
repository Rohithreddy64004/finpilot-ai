"""Manually verify Nugen inference after configuring a key and live model ID."""

import asyncio
import sys
from collections.abc import Iterable

from backend.app.config import Settings, get_settings
from backend.app.nugen.client import NugenClient
from backend.app.nugen.errors import NugenAPIError, NugenConfigurationError, NugenProtocolError
from backend.app.nugen.schemas import BaseModelSummary, ChatCompletionRequest, NugenMessage


def _diagnostic_model_list(
    models: Iterable[BaseModelSummary],
    *,
    secret: str,
    max_rows: int = 30,
) -> str:
    """Show only the model ID and readiness fields needed to debug selection."""

    rows = list(models)
    if not rows:
        return "Nugen returned no base models."

    def safe_value(value: str) -> str:
        if secret:
            value = value.replace(secret, "[REDACTED]")
        return repr(value[:160])

    lines = ["Returned base models (model_id | type | inference_ready):"]
    for model in rows[:max_rows]:
        readiness = (
            "missing (defaults to false)"
            if "inference_ready" not in model.model_fields_set
            else str(model.inference_ready).lower()
        )
        lines.append(
            f"- {safe_value(model.model_id)} | {safe_value(model.type)} | {readiness}"
        )
    if len(rows) > max_rows:
        lines.append(f"- {len(rows) - max_rows} additional model(s) omitted")
    return "\n".join(lines)


async def run_inference_check(settings: Settings) -> tuple[str, str]:
    """Verify a configured model against Nugen's live base-model list, then infer."""

    model_id = (settings.nugen_test_model_id or "").strip()
    if not model_id:
        raise NugenConfigurationError(
            "NUGEN_TEST_MODEL_ID is missing. Set it to a model_id returned by Nugen's base-model list."
        )

    secret = (
        settings.nugen_api_key.get_secret_value()
        if settings.nugen_api_key is not None
        else ""
    )
    async with NugenClient(settings) as client:
        offset = 0
        page_size = 100
        models: list[BaseModelSummary] = []
        while True:
            # Fetch unfiltered rows so server-side filters cannot hide the configured
            # ID before diagnostics can show its documented task/readiness fields.
            page = await client.list_base_models(limit=page_size, offset=offset)
            models.extend(page.models)
            if len(page.models) < page_size:
                break
            offset += page_size

        verified_model = next((model for model in models if model.model_id == model_id), None)
        failure_reason: str | None = None
        if verified_model is None:
            failure_reason = "the ID was not returned by GET /api/v3/models/base"
        elif verified_model.type != "text-generation":
            failure_reason = "the returned type is not 'text-generation'"
        elif "inference_ready" not in verified_model.model_fields_set:
            failure_reason = (
                "Nugen omitted inference_ready for this row; the documented default is false"
            )
        elif verified_model.inference_ready is not True:
            failure_reason = "Nugen did not explicitly mark inference_ready as true"

        if failure_reason is not None:
            diagnostics = _diagnostic_model_list(models, secret=secret)
            raise NugenConfigurationError(
                "NUGEN_TEST_MODEL_ID is not a verified inference-ready text-generation "
                f"base model: {failure_reason}.\n{diagnostics}"
            )

        # The guard above establishes this value, including the presence of the
        # readiness field. Keep the type narrowing explicit for static checkers.
        assert verified_model is not None

        response = await client.chat_completions(
            ChatCompletionRequest(
                model=verified_model.model_id,
                messages=[
                    NugenMessage(
                        role="user",
                        content="What is compound interest? Answer in one sentence.",
                    )
                ],
                max_tokens=120,
                temperature=0.2,
            )
        )
        if not response.choices or not response.choices[0].message.content:
            raise NugenProtocolError("Nugen returned no answer in the first completion choice.")
        return verified_model.model_id, response.choices[0].message.content


def main() -> int:
    settings = get_settings()
    if settings.nugen_api_key is None or not settings.nugen_api_key.get_secret_value().strip():
        print("NUGEN_API_KEY is missing. Configure it in .env before running this command.", file=sys.stderr)
        return 2
    if settings.nugen_test_model_id is None or not settings.nugen_test_model_id.strip():
        print("NUGEN_TEST_MODEL_ID is missing. Set it to a model_id from Nugen's base-model list.", file=sys.stderr)
        return 2

    try:
        model_id, answer = asyncio.run(run_inference_check(settings))
    except (NugenAPIError, NugenConfigurationError) as exc:
        print(f"Nugen inference check failed: {exc}", file=sys.stderr)
        return 1

    print(f"Model ID: {model_id}")
    print(f"Answer: {answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
