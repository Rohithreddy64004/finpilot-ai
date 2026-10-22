import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException

from backend.app.config import Settings, get_settings
from backend.app.main import add_frontend_cors, document_store_error_handler
from backend.app.rate_limit import enforce_api_rate_limit
from backend.app.source_documents import SourceDocumentStoreError


ALLOWED_ORIGIN = "https://finpilot.example"


class AlwaysLimited:
    def check(self, _key: str) -> tuple[bool, int]:
        return False, 9


def create_cors_app(frontend_origins: str) -> FastAPI:
    runtime_settings = Settings(_env_file=None, frontend_origins=frontend_origins)
    test_app = FastAPI()
    add_frontend_cors(test_app, runtime_settings)
    test_app.dependency_overrides[get_settings] = lambda: runtime_settings
    test_app.add_exception_handler(SourceDocumentStoreError, document_store_error_handler)
    test_app.state.api_rate_limiter = AlwaysLimited()

    @test_app.get("/health")
    async def health():
        return {"status": "ok"}

    @test_app.post("/api/ask", dependencies=[Depends(enforce_api_rate_limit)])
    async def ask():
        return {"answer": "unreachable"}

    @test_app.post("/api/feedback")
    async def feedback():
        raise SourceDocumentStoreError("Feedback store unavailable.")

    @test_app.get("/error-503")
    async def error_503():
        raise HTTPException(status_code=503, detail="Unavailable.")

    return test_app


@pytest.mark.asyncio
async def test_allowed_origin_gets_cors_headers_and_errors_keep_them() -> None:
    test_app = create_cors_app(ALLOWED_ORIGIN)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        health = await client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
        limited = await client.post("/api/ask", headers={"Origin": ALLOWED_ORIGIN}, json={})
        failed = await client.post("/api/feedback", headers={"Origin": ALLOWED_ORIGIN}, json={})
        handled_503 = await client.get("/error-503", headers={"Origin": ALLOWED_ORIGIN})

    assert health.status_code == 200
    assert limited.status_code == 429
    assert failed.status_code == 503
    assert handled_503.status_code == 503
    for response in (health, limited, failed, handled_503):
        assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
        assert "access-control-allow-credentials" not in response.headers


@pytest.mark.asyncio
async def test_other_origin_does_not_receive_cors_grant() -> None:
    test_app = create_cors_app(ALLOWED_ORIGIN)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get("/health", headers={"Origin": "https://other.example"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/ask", "/api/feedback"])
async def test_post_preflight_is_allowed_for_api_routes(path: str) -> None:
    test_app = create_cors_app(ALLOWED_ORIGIN)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.options(
            path,
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()


@pytest.mark.asyncio
async def test_empty_origins_means_no_cors_middleware() -> None:
    test_app = create_cors_app("")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get("/health", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
