import httpx
import pytest

from backend.app.config import Settings
from backend.app.nugen.alignment_service import NugenAlignmentService
from backend.app.nugen.client import NugenClient, NugenUploadFile
from backend.app.nugen.errors import (
    NugenAuthenticationError,
    NugenConfigurationError,
    NugenProtocolError,
    NugenRateLimitError,
    NugenServerError,
    NugenTimeoutError,
)
from backend.app.nugen.schemas import ChatCompletionRequest, CreateAlignmentRequest, NugenMessage


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, nugen_api_key="mock-secret", **overrides)


@pytest.mark.asyncio
async def test_inference_settings_uses_bearer_auth_and_documented_route() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == "https://api.nugen.in/api/v3/inference/capture"
        assert request.headers["Authorization"] == "Bearer mock-secret"
        return httpx.Response(200, json={"enabled": False, "models": {"fixture-model-id": True}})

    async with NugenClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        response = await client.get_inference_settings()

    assert response.enabled is False
    assert response.models == {"fixture-model-id": True}


@pytest.mark.asyncio
async def test_list_base_models_sends_only_documented_query_parameters() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/models/base"
        assert dict(request.url.params) == {
            "alignment_ready": "true",
            "inference_ready": "true",
            "type": "text-generation",
            "limit": "50",
            "offset": "0",
        }
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model_id": "fixture-model-id",
                        "model_name": "Fixture model",
                        "type": "text-generation",
                        "alignment_ready": True,
                        "inference_ready": True,
                        "available_on_request": False,
                    }
                ]
            },
        )

    async with NugenClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        response = await client.list_base_models(
            alignment_ready=True,
            inference_ready=True,
            model_type="text-generation",
            limit=50,
        )

    assert response.models[0].model_id == "fixture-model-id"
    assert response.models[0].inference_ready is True


@pytest.mark.asyncio
async def test_list_base_models_requires_openapi_models_envelope_not_data_wrapper() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"models": []}})

    async with NugenClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NugenProtocolError, match="documented response schema"):
            await client.list_base_models()


@pytest.mark.asyncio
async def test_chat_completion_posts_documented_non_streaming_payload() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v3/inference/chat/completions"
        assert request.headers["Authorization"] == "Bearer mock-secret"
        assert await request.aread() == (
            b'{"model":"fixture-model-id","messages":[{"role":"user",'
            b'"content":"What is compound interest?"}],"max_tokens":120,'
            b'"prompt_truncate_len":1500,"temperature":0.2,"stream":false,"n":1}'
        )
        return httpx.Response(
            200,
            json={
                "id": "fixture-response",
                "object": "chat.completion",
                "created": 1704123600.0,
                "model": "fixture-model-id",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Compound interest includes interest earned on prior interest.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 10, "total_tokens": 22},
            },
        )

    async with NugenClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        response = await client.chat_completions(
            ChatCompletionRequest(
                model="fixture-model-id",
                messages=[NugenMessage(role="user", content="What is compound interest?")],
                max_tokens=120,
                temperature=0.2,
            )
        )

    assert response.choices[0].message.content == (
        "Compound interest includes interest earned on prior interest."
    )
    assert response.usage is not None
    assert response.usage.total_tokens == 22


@pytest.mark.asyncio
async def test_upload_encodes_files_categories_and_names_as_multipart_arrays() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v3/documents/create"
        assert request.headers["content-type"].startswith("multipart/form-data;")
        body = await request.aread()
        assert body.count(b'name="files"') == 2
        assert body.count(b'name="categories"') == 2
        assert body.count(b'name="names"') == 2
        assert b"first.txt" in body and b"second.txt" in body
        return httpx.Response(200, json={"document_ids": ["fixture-doc-1", "fixture-doc-2"]})

    async with NugenClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        response = await NugenAlignmentService(client).upload_documents(
            [
                NugenUploadFile("first.txt", b"first document"),
                NugenUploadFile("second.txt", b"second document"),
            ],
            categories=["tax", "tax"],
            names=["First", "Second"],
        )

    assert response.document_ids == ["fixture-doc-1", "fixture-doc-2"]


@pytest.mark.asyncio
async def test_alignment_service_upload_status_and_alignment_calls_are_explicit() -> None:
    calls: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/v3/documents/fixture-doc/status":
            return httpx.Response(
                200,
                json={
                    "status": "READY",
                    "document_id": "fixture-doc",
                    "created_at": "2026-01-01T00:00:00Z",
                    "completed_at": "2026-01-01T00:01:00Z",
                    "updated_at": "2026-01-01T00:01:00Z",
                    "progress": None,
                    "eta_seconds": None,
                },
            )
        if request.url.path == "/api/v3/alignment-projects/create":
            assert request.method == "POST"
            assert await request.aread() == (
                b'{"alignment_name":"Fixture alignment","base_model_id":"fixture-base-id",'
                b'"document_ids":["fixture-doc"]}'
            )
            return httpx.Response(
                200,
                json={"alignment_id": "fixture-alignment", "status": "PROCESSING"},
            )
        if request.url.path == "/api/v3/alignment-projects/fixture-alignment/status":
            return httpx.Response(
                200,
                json={
                    "alignment_id": "fixture-alignment",
                    "status": "READY",
                    "early_deployable": False,
                },
            )
        raise AssertionError(f"unexpected route: {request.method} {request.url.path}")

    async with NugenClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        service = NugenAlignmentService(client)
        document_status = await service.document_status("fixture-doc")
        created = await service.create_alignment(
            CreateAlignmentRequest(
                alignment_name="Fixture alignment",
                base_model_id="fixture-base-id",
                document_ids=["fixture-doc"],
            )
        )
        alignment_status = await service.alignment_status(created.alignment_id)

    assert document_status.status == "READY"
    assert created.status == "PROCESSING"
    assert alignment_status.status == "READY"
    assert calls == [
        ("GET", "/api/v3/documents/fixture-doc/status"),
        ("POST", "/api/v3/alignment-projects/create"),
        ("GET", "/api/v3/alignment-projects/fixture-alignment/status"),
    ]


@pytest.mark.asyncio
async def test_alignment_create_rejects_undocumented_initial_provider_status() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v3/alignment-projects/create"
        return httpx.Response(
            200,
            json={"alignment_id": "fixture-alignment", "status": "READY"},
        )

    async with NugenClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NugenProtocolError, match="documented response schema"):
            await client.create_alignment(
                CreateAlignmentRequest(
                    alignment_name="Fixture alignment",
                    base_model_id="fixture-base-id",
                    document_ids=["fixture-doc"],
                )
            )


@pytest.mark.asyncio
async def test_list_documents_parses_document_rows_and_category_counts() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v3/documents/list"
        assert dict(request.url.params) == {
            "limit": "10",
            "offset": "0",
            "status": "READY",
        }
        return httpx.Response(
            200,
            json={
                "documents": [
                    {
                        "document_id": "fixture-doc",
                        "document_name": "Fixture document",
                        "status": "READY",
                        "created_at": "2026-01-01T00:00:00Z",
                        "categories": ["tax"],
                    }
                ],
                "categories": [{"name": "tax", "count": 1}],
            },
        )

    async with NugenClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        response = await NugenAlignmentService(client).list_documents(status="READY")

    assert response.documents[0].created_at.isoformat() == "2026-01-01T00:00:00+00:00"
    assert response.categories[0].name == "tax"
    assert response.categories[0].count == 1


@pytest.mark.asyncio
async def test_lists_aligned_models_and_checks_deployment_status() -> None:
    calls: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/v3/models/aligned":
            return httpx.Response(
                200,
                json={
                    "domain_aligned_models": [
                        {
                            "model_id": "fixture-aligned-model",
                            "model_name": "Fixture model",
                            "deployment_status": "DEPLOYED",
                            "base_model_id": "fixture-base-id",
                            "alignment_id": "fixture-alignment",
                            "created_at": "2026-01-01T00:00:00Z",
                        }
                    ]
                },
            )
        if request.url.path == "/api/v3/models/fixture-aligned-model/deployment/status":
            return httpx.Response(
                200,
                json={
                    "model_id": "fixture-aligned-model",
                    "status": "DEPLOYED",
                    "error": None,
                    "created_at": "2026-01-01T00:00:00Z",
                    "completed_at": "2026-01-01T00:01:00Z",
                    "updated_at": "2026-01-01T00:01:00Z",
                    "progress": None,
                    "eta_seconds": None,
                },
            )
        raise AssertionError(f"unexpected route: {request.method} {request.url.path}")

    async with NugenClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        aligned = await client.list_aligned_models(deployment_status="DEPLOYED")
        status = await client.get_deployment_status("fixture-aligned-model")

    assert aligned.domain_aligned_models[0].model_id == "fixture-aligned-model"
    assert status.status == "DEPLOYED"
    assert calls == [
        ("GET", "/api/v3/models/aligned"),
        ("GET", "/api/v3/models/fixture-aligned-model/deployment/status"),
    ]


@pytest.mark.asyncio
async def test_deploy_is_an_explicit_post_with_no_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v3/models/fixture-aligned-model/deployment"
        assert await request.aread() == b""
        return httpx.Response(200, json={"model_id": "fixture-aligned-model"})

    async with NugenClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        model_id = await client.deploy_aligned_model("fixture-aligned-model")

    assert model_id == "fixture-aligned-model"


@pytest.mark.asyncio
async def test_auth_error_never_exposes_api_key_or_provider_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "mock-secret was rejected"})

    async with NugenClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NugenAuthenticationError) as raised:
            await client.get_inference_settings()

    assert "NUGEN_API_KEY" in str(raised.value)
    assert "mock-secret" not in str(raised.value)
    assert "mock-secret" not in repr(raised.value)


@pytest.mark.asyncio
async def test_rate_limit_includes_retry_after_without_retrying() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "12"}, json={"detail": "slow down"})

    async with NugenClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NugenRateLimitError) as raised:
            await client.list_base_models()

    assert calls == 1
    assert raised.value.retry_after == "12"
    assert "429" in str(raised.value)


@pytest.mark.asyncio
async def test_get_retries_one_transient_server_failure() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"enabled": False, "models": {}})

    async with NugenClient(
        make_settings(nugen_get_retries=1),
        transport=httpx.MockTransport(handler),
    ) as client:
        response = await client.get_inference_settings()

    assert response.enabled is False
    assert calls == 2


@pytest.mark.asyncio
async def test_post_does_not_retry_transient_server_failure() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    async with NugenClient(
        make_settings(nugen_get_retries=1),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(NugenServerError):
            await client.chat_completions(
                ChatCompletionRequest(
                    model="fixture-model-id",
                    messages=[NugenMessage(role="user", content="hello")],
                )
            )

    assert calls == 1


@pytest.mark.asyncio
async def test_timeout_maps_to_clear_safe_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("request timed out", request=request)

    async with NugenClient(
        make_settings(nugen_get_retries=0),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(NugenTimeoutError) as raised:
            await client.get_inference_settings()

    assert str(raised.value) == "Nugen request timed out."
    assert "mock-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_missing_api_key_fails_before_http_request() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"enabled": False, "models": {}})

    client = NugenClient(
        Settings(_env_file=None, nugen_api_key=None),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(NugenConfigurationError, match="NUGEN_API_KEY"):
        await client.get_inference_settings()

    assert calls == 0


@pytest.mark.asyncio
async def test_invalid_success_payload_maps_to_protocol_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    async with NugenClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NugenProtocolError, match="documented response schema"):
            await client.get_inference_settings()
