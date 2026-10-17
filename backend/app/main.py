"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.app.alignment_jobs import MongoAlignmentJobRepository
from backend.app.config import Settings, get_settings
from backend.app.inference_api import router as inference_router
from backend.app.nugen.errors import (
    NugenAPIError,
    NugenAuthenticationError,
    NugenConfigurationError,
    NugenNotFoundError,
    NugenRateLimitError,
    NugenTimeoutError,
)
from backend.app.schemas import APIIndexResponse, HealthResponse, ReadinessResponse
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


@asynccontextmanager
async def lifespan(application: FastAPI):
    yield
    repository = getattr(application.state, "source_document_repository", None)
    if isinstance(repository, MongoSourceDocumentRepository):
        await repository.close()
    alignment_repository = getattr(application.state, "alignment_job_repository", None)
    if isinstance(alignment_repository, MongoAlignmentJobRepository):
        await alignment_repository.close()


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.cors_allowed_origins.split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)
app.include_router(workflow_router)
app.include_router(inference_router)


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """Report that the API process is responding."""

    return HealthResponse(service=settings.app_name)


@app.get("/", response_model=APIIndexResponse, include_in_schema=False)
async def api_index() -> APIIndexResponse:
    """Point browser visits at the API docs and health endpoints."""

    return APIIndexResponse(service=settings.app_name)


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
