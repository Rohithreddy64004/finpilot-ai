import httpx
import pytest

from backend.app.config import Settings, get_settings
from backend.app.main import app
from backend.app.source_documents import SourceDocumentStoreError
from backend.app.workflow_api import get_document_repository


class ReadyRepository:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def check_ready(self) -> None:
        self.calls += 1
        if self.fail:
            raise SourceDocumentStoreError("The MongoDB document registry is unavailable.")


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "FinPilot AI API"}


@pytest.mark.asyncio
async def test_api_root_points_to_docs_and_health_routes() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "service": "FinPilot AI API",
        "docs_path": "/docs",
        "health_path": "/health",
        "readiness_path": "/ready",
    }


@pytest.mark.asyncio
async def test_readiness_checks_mongo_and_configured_nugen_key(monkeypatch) -> None:
    repository = ReadyRepository()
    monkeypatch.setitem(app.dependency_overrides, get_document_repository, lambda: repository)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_settings,
        lambda: Settings(_env_file=None, nugen_api_key="test-only-key"),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "FinPilot AI API",
        "mongodb": "ready",
        "nugen_api_credential": "configured",
    }
    assert repository.calls == 1


@pytest.mark.asyncio
async def test_readiness_fails_safely_when_nugen_key_is_missing(monkeypatch) -> None:
    repository = ReadyRepository()
    monkeypatch.setitem(app.dependency_overrides, get_document_repository, lambda: repository)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_settings,
        lambda: Settings(_env_file=None, nugen_api_key=None),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Nugen API credentials are not configured on the server."
    }
    assert repository.calls == 0


@pytest.mark.asyncio
async def test_readiness_returns_safe_mongo_failure(monkeypatch) -> None:
    repository = ReadyRepository(fail=True)
    monkeypatch.setitem(app.dependency_overrides, get_document_repository, lambda: repository)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_settings,
        lambda: Settings(_env_file=None, nugen_api_key="test-only-key"),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "The MongoDB document registry is unavailable."
    }
    assert repository.calls == 1


@pytest.mark.asyncio
async def test_frontend_origin_is_allowed_for_json_post_preflight() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/api/inference",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.asyncio
async def test_unknown_frontend_origin_is_not_allowed() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/api/inference",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
