"""Async client for the documented Nugen API v3 surface used by FinPilot."""

import asyncio
import secrets
from dataclasses import dataclass
from collections.abc import AsyncIterator
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from backend.app.config import Settings, get_settings
from backend.app.nugen.errors import (
    NugenAPIError,
    NugenAuthenticationError,
    NugenConfigurationError,
    NugenNotFoundError,
    NugenProtocolError,
    NugenRateLimitError,
    NugenRequestError,
    NugenServerError,
    NugenTimeoutError,
    NugenTransportError,
)
from backend.app.nugen.schemas import (
    AlignedModelsResponse,
    AlignmentStatusResponse,
    BaseModelsResponse,
    ChatCompletionRequest,
    ChatCompletionResponse,
    CreateAlignmentRequest,
    CreateAlignmentResponse,
    DeployAlignedModelResponse,
    DeploymentStatusResponse,
    DeploymentStatusValue,
    DocumentStatusResponse,
    DocumentStatusValue,
    DocumentsResponse,
    InferenceSettingsResponse,
    UploadedDocumentsResponse,
)


ResponseSchema = TypeVar("ResponseSchema", bound=BaseModel)
_API_V3 = "api/v3"


class _MultipartStream(httpx.AsyncByteStream):
    """Small async multipart stream using the verified OpenAPI form fields."""

    def __init__(
        self,
        data: list[tuple[str, str]],
        files: list[tuple[str, tuple[str, bytes, str | None]]],
    ) -> None:
        self.boundary = f"finpilot-{secrets.token_hex(16)}"
        self._chunks: list[bytes] = []

        for field_name, (filename, content, content_type) in files:
            safe_filename = self._quoted_header_value(filename)
            safe_type = content_type or "application/octet-stream"
            if "\r" in safe_type or "\n" in safe_type:
                raise ValueError("file content_type must not contain newlines")
            self._chunks.extend(
                [
                    f"--{self.boundary}\r\n".encode(),
                    (
                        f'Content-Disposition: form-data; name="{field_name}"; '
                        f'filename="{safe_filename}"\r\n'
                    ).encode("utf-8"),
                    f"Content-Type: {safe_type}\r\n\r\n".encode("ascii"),
                    content,
                    b"\r\n",
                ]
            )

        for field_name, value in data:
            safe_field_name = self._quoted_header_value(field_name)
            self._chunks.extend(
                [
                    f"--{self.boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{safe_field_name}"\r\n\r\n'.encode(
                        "utf-8"
                    ),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )
        self._chunks.append(f"--{self.boundary}--\r\n".encode())
        self.content_length = sum(len(chunk) for chunk in self._chunks)

    @staticmethod
    def _quoted_header_value(value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("multipart header values must not contain newlines")
        return value.replace("\\", "\\\\").replace('"', '\\"')

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class NugenUploadFile:
    """One file part for the documented multipart upload endpoint."""

    filename: str
    content: bytes
    content_type: str | None = None


class NugenClient:
    """Credential-safe async Nugen client with explicit timeout and safe retries.

    Only GET calls are retried, because their documented operations are reads.
    POST inference, upload, alignment, and deployment calls are never retried to
    avoid duplicating work or consuming credits after an uncertain response.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._transport = transport
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "NugenClient":
        self._get_http_client()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            secret = self.settings.nugen_api_key
            if secret is None or not secret.get_secret_value().strip():
                raise NugenConfigurationError(
                    "NUGEN_API_KEY is not configured. Set it in the environment or local .env file."
                )
            self._http = httpx.AsyncClient(
                base_url=f"{self.settings.nugen_api_base_url.rstrip('/')}/",
                headers={"Authorization": f"Bearer {secret.get_secret_value()}"},
                timeout=httpx.Timeout(self.settings.nugen_timeout_seconds),
                follow_redirects=False,
                transport=self._transport,
            )
        return self._http

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: list[tuple[str, str]] | None = None,
        files: list[tuple[str, tuple[str, bytes, str | None]]] | None = None,
    ) -> dict[str, Any]:
        client = self._get_http_client()
        retry_count = self.settings.nugen_get_retries if method.upper() == "GET" else 0

        for attempt in range(retry_count + 1):
            try:
                request_kwargs: dict[str, Any] = {"params": params, "json": json_body}
                if files is not None:
                    multipart = _MultipartStream(data or [], files)
                    request_kwargs = {
                        "params": params,
                        "content": multipart,
                        "headers": {
                            "Content-Type": f"multipart/form-data; boundary={multipart.boundary}",
                            "Content-Length": str(multipart.content_length),
                        },
                    }
                response = await client.request(method, path, **request_kwargs)
            except httpx.TimeoutException:
                if method.upper() == "GET" and attempt < retry_count:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                raise NugenTimeoutError("Nugen request timed out.") from None
            except httpx.RequestError:
                if method.upper() == "GET" and attempt < retry_count:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                raise NugenTransportError(
                    "Could not connect securely to the Nugen API."
                ) from None

            if response.status_code >= 500 and attempt < retry_count:
                await asyncio.sleep(0.1 * (attempt + 1))
                continue
            self._raise_for_status(response, method, path)

            try:
                payload = response.json()
            except ValueError:
                raise NugenProtocolError(
                    f"Nugen returned a non-JSON response for {method} {path}."
                ) from None
            if not isinstance(payload, dict):
                raise NugenProtocolError(
                    f"Nugen returned a non-object JSON response for {method} {path}."
                )
            return payload

        raise NugenAPIError(f"Nugen request failed for {method} {path}.")

    @staticmethod
    def _raise_for_status(response: httpx.Response, method: str, path: str) -> None:
        status = response.status_code
        if status < 400:
            return

        operation = f"{method} {path}"
        if status in (401, 403):
            raise NugenAuthenticationError(
                f"Nugen authentication failed for {operation} (HTTP {status}); check NUGEN_API_KEY.",
                status_code=status,
            )
        if status == 429:
            raise NugenRateLimitError(
                f"Nugen rate limit reached for {operation} (HTTP 429).",
                status_code=status,
                retry_after=response.headers.get("Retry-After"),
            )
        if status == 404:
            raise NugenNotFoundError(
                f"Nugen resource was not found for {operation} (HTTP 404).",
                status_code=status,
            )
        if status in (408, 504):
            raise NugenTimeoutError(
                f"Nugen timed out for {operation} (HTTP {status}).",
                status_code=status,
            )
        if status >= 500:
            raise NugenServerError(
                f"Nugen returned a server error for {operation} (HTTP {status}).",
                status_code=status,
            )
        raise NugenRequestError(
            f"Nugen rejected {operation} (HTTP {status}).",
            status_code=status,
        )

    @staticmethod
    def _parse_response(schema: type[ResponseSchema], payload: dict[str, Any], path: str) -> ResponseSchema:
        try:
            return schema.model_validate(payload)
        except ValidationError:
            raise NugenProtocolError(
                f"Nugen returned data outside the documented response schema for {path}."
            ) from None

    @staticmethod
    def _pagination(limit: int, offset: int) -> None:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must be zero or greater")

    async def get_inference_settings(self) -> InferenceSettingsResponse:
        path = f"{_API_V3}/inference/capture"
        payload = await self._request_json("GET", path)
        return self._parse_response(InferenceSettingsResponse, payload, path)

    async def list_base_models(
        self,
        *,
        alignment_ready: bool | None = None,
        inference_ready: bool | None = None,
        model_type: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> BaseModelsResponse:
        self._pagination(limit, offset)
        params = {
            key: value
            for key, value in {
                "alignment_ready": alignment_ready,
                "inference_ready": inference_ready,
                "type": model_type,
                "limit": limit,
                "offset": offset,
            }.items()
            if value is not None
        }
        path = f"{_API_V3}/models/base"
        payload = await self._request_json("GET", path, params=params)
        return self._parse_response(BaseModelsResponse, payload, path)

    async def chat_completions(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        path = f"{_API_V3}/inference/chat/completions"
        payload = await self._request_json(
            "POST",
            path,
            json_body=request.model_dump(mode="json", exclude_none=True),
        )
        return self._parse_response(ChatCompletionResponse, payload, path)

    async def upload_documents(
        self,
        files_to_upload: list[NugenUploadFile],
        *,
        categories: list[str] | None = None,
        names: list[str] | None = None,
    ) -> UploadedDocumentsResponse:
        if not files_to_upload:
            raise ValueError("at least one file is required")
        file_parts = [
            ("files", (item.filename, item.content, item.content_type))
            for item in files_to_upload
        ]
        form_fields: list[tuple[str, str]] = []
        for category in categories or []:
            form_fields.append(("categories", category))
        for name in names or []:
            form_fields.append(("names", name))

        path = f"{_API_V3}/documents/create"
        payload = await self._request_json(
            "POST",
            path,
            data=form_fields,
            files=file_parts,
        )
        return self._parse_response(UploadedDocumentsResponse, payload, path)

    async def list_documents(
        self,
        *,
        status: DocumentStatusValue | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> DocumentsResponse:
        self._pagination(limit, offset)
        params = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        path = f"{_API_V3}/documents/list"
        payload = await self._request_json("GET", path, params=params)
        return self._parse_response(DocumentsResponse, payload, path)

    async def get_document_status(self, document_id: str) -> DocumentStatusResponse:
        path = f"{_API_V3}/documents/{document_id}/status"
        payload = await self._request_json("GET", path)
        return self._parse_response(DocumentStatusResponse, payload, path)

    async def create_alignment(self, request: CreateAlignmentRequest) -> CreateAlignmentResponse:
        """Start an alignment only when explicitly called by the application."""

        path = f"{_API_V3}/alignment-projects/create"
        payload = await self._request_json(
            "POST",
            path,
            json_body=request.model_dump(mode="json", exclude_none=True),
        )
        return self._parse_response(CreateAlignmentResponse, payload, path)

    async def get_alignment_status(self, alignment_id: str) -> AlignmentStatusResponse:
        path = f"{_API_V3}/alignment-projects/{alignment_id}/status"
        payload = await self._request_json("GET", path)
        return self._parse_response(AlignmentStatusResponse, payload, path)

    async def list_aligned_models(
        self,
        *,
        deployment_status: DeploymentStatusValue | None = None,
        base_model_id: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> AlignedModelsResponse:
        self._pagination(limit, offset)
        params = {
            key: value
            for key, value in {
                "deployment_status": deployment_status,
                "base_model_id": base_model_id,
                "limit": limit,
                "offset": offset,
            }.items()
            if value is not None
        }
        path = f"{_API_V3}/models/aligned"
        payload = await self._request_json("GET", path, params=params)
        return self._parse_response(AlignedModelsResponse, payload, path)

    async def deploy_aligned_model(
        self,
        model_id: str,
        *,
        early: bool = False,
    ) -> str:
        """Request deployment explicitly; this method is never called on startup."""

        path = f"{_API_V3}/models/{model_id}/deployment"
        payload = await self._request_json(
            "POST",
            path,
            params={"early": True} if early else None,
        )
        response = self._parse_response(DeployAlignedModelResponse, payload, path)
        return response.model_id

    async def get_deployment_status(self, model_id: str) -> DeploymentStatusResponse:
        path = f"{_API_V3}/models/{model_id}/deployment/status"
        payload = await self._request_json("GET", path)
        return self._parse_response(DeploymentStatusResponse, payload, path)
