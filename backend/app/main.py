"""FastAPI application entry point."""

from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.alignment_jobs import MongoAlignmentJobRepository
from backend.app.ask_api import router as ask_router
from backend.app.config import Settings, get_settings
from backend.app.content_seeding import seed_registered_content
from backend.app.inference_api import router as inference_router
from backend.app.interactions import MongoInteractionRepository
from backend.app.interaction_api import router as interaction_router
from backend.app.nugen.errors import (
    NugenAPIError,
    NugenAuthenticationError,
    NugenConfigurationError,
    NugenNotFoundError,
    NugenRateLimitError,
    NugenTimeoutError,
)
from backend.app.schemas import HealthResponse, ReadinessResponse
from backend.app.request_limits import InferenceBodyLimitMiddleware
from backend.app.source_content_store import LocalSourceContentStore
from backend.app.source_documents import (
    MongoSourceDocumentRepository,
    SourceDocumentRepository,
    SourceDocumentStoreError,
)
from backend.app.workflow_api import (
    get_document_repository,
    router as workflow_router,
)

settings = get_settings()
logger = logging.getLogger(__name__)
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def cors_origins_for(settings: Settings) -> list[str]:
    """Return the configured exact frontend origins, or no origins when unset."""

    if not settings.frontend_origins:
        return []
    return settings.frontend_origins.split(",")


def add_frontend_cors(application: FastAPI, runtime_settings: Settings) -> None:
    """Add optional CORS support with the exact configured frontend origins."""

    origins = cors_origins_for(runtime_settings)
    if not origins:
        return
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )


@asynccontextmanager
async def lifespan(application: FastAPI):
    if settings.seed_content_on_startup:
        repository = MongoSourceDocumentRepository(settings)
        application.state.source_document_repository = repository
        try:
            summary = await seed_registered_content(
                repository,
                LocalSourceContentStore(settings),
            )
            logger.info(
                "Optional source-content seed completed: seeded=%s already_present=%s skipped=%s errors=%s",
                summary.seeded,
                summary.already_present,
                (
                    summary.skipped_not_ready
                    + summary.skipped_size_mismatch
                    + summary.skipped_invalid_text
                    + summary.skipped_missing_file
                ),
                summary.storage_errors,
            )
        except Exception as exc:
            # Exception messages from drivers can contain connection details.
            logger.warning(
                "Optional source-content seed failed safely: error_type=%s",
                type(exc).__name__,
            )
    yield
    repository = getattr(application.state, "source_document_repository", None)
    if isinstance(repository, MongoSourceDocumentRepository):
        await repository.close()
    alignment_repository = getattr(application.state, "alignment_job_repository", None)
    if isinstance(alignment_repository, MongoAlignmentJobRepository):
        await alignment_repository.close()
    interaction_repository = getattr(application.state, "interaction_repository", None)
    if isinstance(interaction_repository, MongoInteractionRepository):
        await interaction_repository.close()


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
app.add_middleware(InferenceBodyLimitMiddleware)
add_frontend_cors(app, settings)
app.include_router(workflow_router)
app.include_router(inference_router)
app.include_router(ask_router)
app.include_router(interaction_router)
if (FRONTEND_DIST / "assets").is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIST / "assets"),
        name="frontend-assets",
    )


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """Keepalive-safe process check; it does not call MongoDB or Nugen."""

    return HealthResponse(service=settings.app_name)


@app.get("/", include_in_schema=False)
async def frontend_index():
    """Serve the compiled React UI when present, with a helpful local-dev response."""

    index_path = FRONTEND_DIST / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return HTMLResponse(
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<title>FinPilot AI</title><body><h1>FinPilot API is running</h1>"
        "<p>Build the React client with <code>cd frontend; npm run build</code>, "
        "or run Vite with <code>npm run dev</code>.</p>"
        "<p><a href='/docs'>API documentation</a> · <a href='/health'>Health</a></p>"
        "</body></html>",
        status_code=200,
    )


@app.get("/ready", response_model=ReadinessResponse, tags=["health"])
async def readiness_check(
    repository: SourceDocumentRepository = Depends(get_document_repository),
    runtime_settings: Settings = Depends(get_settings),
) -> ReadinessResponse:
    """Check Mongo readiness and local Nugen credential configuration only.

    This endpoint does not call Nugen or verify configured model readiness.
    """

    api_key = runtime_settings.nugen_api_key
    if api_key is None or not api_key.get_secret_value().strip():
        raise HTTPException(
            status_code=503,
            detail="Nugen API credentials are not configured on the server.",
        )
    await repository.check_ready()
    return ReadinessResponse(service=runtime_settings.app_name)


@app.exception_handler(NugenConfigurationError)
async def nugen_configuration_error_handler(
    _request: Request,
    _exc: NugenConfigurationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "Nugen API configuration is incomplete on the server."},
    )


@app.exception_handler(NugenAPIError)
async def nugen_api_error_handler(_request: Request, exc: NugenAPIError) -> JSONResponse:
    if isinstance(exc, NugenNotFoundError):
        status_code = 404
    elif isinstance(exc, NugenRateLimitError):
        status_code = 429
    elif isinstance(exc, NugenTimeoutError):
        status_code = 504
    elif isinstance(exc, NugenAuthenticationError):
        status_code = 502
    else:
        status_code = 502
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


@app.exception_handler(SourceDocumentStoreError)
async def document_store_error_handler(
    _request: Request,
    exc: SourceDocumentStoreError,
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})
