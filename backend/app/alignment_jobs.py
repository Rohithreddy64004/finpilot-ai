"""Persistent repository for explicitly submitted Nugen alignment jobs."""

from __future__ import annotations

from typing import Protocol

from fastapi import Request

from backend.app.config import Settings, get_settings
from backend.app.schemas import AlignmentJobRecord, ExpertName
from backend.app.source_documents import SourceDocumentStoreError


class AlignmentJobStoreError(SourceDocumentStoreError):
    """Safe error raised when alignment tracking cannot be persisted or read."""


class AlignmentJobRepository(Protocol):
    async def save(self, record: AlignmentJobRecord) -> None: ...

    async def get_by_tracking_id(self, tracking_id: str) -> AlignmentJobRecord | None: ...

    async def get_by_alignment_id(self, alignment_id: str) -> AlignmentJobRecord | None: ...

    async def find_domain_model(
        self,
        domain: ExpertName,
        model_id: str,
    ) -> AlignmentJobRecord | None: ...


class MongoAlignmentJobRepository:
    """Async MongoDB storage, separate from the immutable source-document registry."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = None
        self._database = None

    def _collection(self):
        if self._database is None:
            try:
                from pymongo import AsyncMongoClient
            except ImportError:
                raise AlignmentJobStoreError(
                    "The async MongoDB driver is unavailable. Install backend requirements."
                ) from None
            try:
                self._client = AsyncMongoClient(
                    self._settings.mongodb_uri.get_secret_value(),
                    serverSelectionTimeoutMS=self._settings.mongodb_timeout_ms,
                    connectTimeoutMS=self._settings.mongodb_timeout_ms,
                    socketTimeoutMS=self._settings.mongodb_timeout_ms,
                )
                self._database = self._client[self._settings.mongodb_database]
            except Exception:
                raise AlignmentJobStoreError(
                    "The MongoDB alignment registry could not be initialized."
                ) from None
        return self._database["finpilot_alignment_jobs"]

    async def save(self, record: AlignmentJobRecord) -> None:
        try:
            await self._collection().replace_one(
                {"tracking_id": record.tracking_id},
                record.model_dump(mode="json"),
                upsert=True,
            )
        except AlignmentJobStoreError:
            raise
        except Exception:
            raise AlignmentJobStoreError(
                "The alignment tracking record could not be saved."
            ) from None

    async def get_by_tracking_id(self, tracking_id: str) -> AlignmentJobRecord | None:
        try:
            raw = await self._collection().find_one({"tracking_id": tracking_id})
            if raw is not None:
                raw.pop("_id", None)
            return AlignmentJobRecord.model_validate(raw) if raw is not None else None
        except AlignmentJobStoreError:
            raise
        except Exception:
            raise AlignmentJobStoreError(
                "The alignment tracking record could not be read."
            ) from None

    async def get_by_alignment_id(self, alignment_id: str) -> AlignmentJobRecord | None:
        try:
            raw = await self._collection().find_one({"alignment_id": alignment_id})
            if raw is not None:
                raw.pop("_id", None)
            return AlignmentJobRecord.model_validate(raw) if raw is not None else None
        except AlignmentJobStoreError:
            raise
        except Exception:
            raise AlignmentJobStoreError(
                "The alignment tracking record could not be read."
            ) from None

    async def find_domain_model(
        self,
        domain: ExpertName,
        model_id: str,
    ) -> AlignmentJobRecord | None:
        try:
            raw = await self._collection().find_one(
                {"domain": domain, "model_id": model_id}
            )
            if raw is not None:
                raw.pop("_id", None)
            return AlignmentJobRecord.model_validate(raw) if raw is not None else None
        except AlignmentJobStoreError:
            raise
        except Exception:
            raise AlignmentJobStoreError(
                "The domain alignment record could not be read."
            ) from None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._database = None


def get_alignment_job_repository(request: Request) -> AlignmentJobRepository:
    repository = getattr(request.app.state, "alignment_job_repository", None)
    if repository is None:
        repository = MongoAlignmentJobRepository(get_settings())
        request.app.state.alignment_job_repository = repository
    return repository
