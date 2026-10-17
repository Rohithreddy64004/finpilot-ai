"""Async persistence for separated expert document sets and source provenance."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

from pydantic import ValidationError

from backend.app.config import Settings, get_settings
from backend.app.schemas import ActiveSourceSelection, ExpertName, SourceDocumentRecord

logger = logging.getLogger(__name__)


class SourceDocumentStoreError(RuntimeError):
    """Safe error raised when the local provenance store is unavailable."""


class ActiveSourceConflictError(SourceDocumentStoreError):
    """An active-source selection changed while a replacement was being validated."""


class SourceDocumentRepository(Protocol):
    async def check_ready(self) -> None: ...

    async def save(self, record: SourceDocumentRecord) -> None: ...

    async def get(self, domain: ExpertName, document_id: str) -> SourceDocumentRecord | None: ...

    async def list(
        self,
        domain: ExpertName,
        *,
        limit: int,
        offset: int,
    ) -> list[SourceDocumentRecord]: ...

    async def update_status(
        self,
        domain: ExpertName,
        document_id: str,
        status: str,
        checked_at: str,
    ) -> None: ...

    async def get_active_source_selection(
        self,
        domain: ExpertName,
    ) -> ActiveSourceSelection | None: ...

    async def activate_source_selection(
        self,
        domain: ExpertName,
        document_ids: list[str],
        *,
        expected_revision: int | None,
        changed_at: datetime,
    ) -> ActiveSourceSelection: ...


class MongoSourceDocumentRepository:
    """Mongo-backed repository with one collection for each expert domain.

    PyMongo's async client is imported lazily, so importing the API and running
    health checks does not require MongoDB to be running.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = None
        self._database = None
        self._indexes_ready = False

    @staticmethod
    def _log_failure(operation: str, exc: Exception, domain: ExpertName | None = None) -> None:
        """Log only safe diagnostic metadata, never URI, record values, or exception text."""

        logger.warning(
            "Mongo source-document operation failed: operation=%s domain=%s error_type=%s",
            operation,
            domain or "all",
            type(exc).__name__,
        )

    @staticmethod
    def _parse_record(raw: dict, *, domain: ExpertName, operation: str) -> SourceDocumentRecord:
        try:
            return SourceDocumentRecord.model_validate(raw)
        except ValidationError as exc:
            # Field paths and Pydantic error types contain no submitted values.
            fields = sorted(
                ".".join(str(part) for part in item.get("loc", ()))
                + ":"
                + str(item.get("type", "validation_error"))
                for item in exc.errors()
            )
            logger.warning(
                "Malformed Mongo provenance record: operation=%s domain=%s error_type=%s fields=%s",
                operation,
                domain,
                type(exc).__name__,
                fields,
            )
            raise SourceDocumentStoreError(
                "A stored document provenance record is malformed. Inspect server diagnostics; no record was changed."
            ) from None

    def _collection(self, domain: ExpertName):
        if self._database is None:
            try:
                from pymongo import AsyncMongoClient
            except ImportError as exc:
                self._log_failure("load_async_driver", exc)
                raise SourceDocumentStoreError(
                    "The async MongoDB driver is unavailable. Install backend requirements."
                ) from None

            try:
                self._client = AsyncMongoClient(
                    self._settings.mongodb_uri.get_secret_value(),
                    serverSelectionTimeoutMS=self._settings.mongodb_timeout_ms,
                    connectTimeoutMS=self._settings.mongodb_timeout_ms,
                    socketTimeoutMS=self._settings.mongodb_timeout_ms,
                )
                self._database = self._client[
                    self._settings.mongodb_database
                ]
            except Exception as exc:
                self._log_failure("initialize_client", exc)
                raise SourceDocumentStoreError(
                    "The MongoDB document registry could not be initialized. Check server configuration and connectivity."
                ) from None

        # Domain names are a closed Literal union, so they cannot inject a
        # collection name. Separate collections keep the three corpora apart.
        return self._database[f"finpilot_source_documents_{domain}"]

    def _active_collection(self):
        # Reuse the same lazily initialized connection without mixing active
        # selection metadata into document provenance rows.
        self._collection("tax")
        assert self._database is not None
        return self._database["finpilot_active_source_selections"]

    async def check_ready(self) -> None:
        try:
            self._collection("tax")
            assert self._client is not None
            await self._client.admin.command({"ping": 1})
        except SourceDocumentStoreError:
            raise
        except Exception as exc:
            self._log_failure("ping", exc)
            raise SourceDocumentStoreError(
                "The MongoDB document registry is unavailable. Check server connectivity."
            ) from None

        if self._indexes_ready:
            return
        try:
            # This preserves existing rows while preventing an accidental
            # duplicate provider ID from replacing or shadowing provenance.
            for domain in ("tax", "investment", "insurance"):
                await self._collection(domain).create_index("document_id", unique=True)
        except Exception as exc:
            self._log_failure("initialize_document_id_indexes", exc)
            raise SourceDocumentStoreError(
                "The MongoDB provenance indexes could not be initialized. Existing records were preserved."
            ) from None
        self._indexes_ready = True

    async def save(self, record: SourceDocumentRecord) -> None:
        try:
            await self._collection(record.domain).insert_one(
                record.model_dump(mode="json")
            )
        except SourceDocumentStoreError:
            raise
        except Exception as exc:
            self._log_failure("insert_provenance", exc, record.domain)
            if type(exc).__name__ == "DuplicateKeyError":
                raise SourceDocumentStoreError(
                    "The Nugen document ID is already registered; existing provenance was not overwritten."
                ) from None
            raise SourceDocumentStoreError(
                "The document provenance record could not be saved. Existing records were not replaced."
            ) from None

    async def get(self, domain: ExpertName, document_id: str) -> SourceDocumentRecord | None:
        try:
            raw = await self._collection(domain).find_one({"document_id": document_id})
            if raw is not None:
                raw.pop("_id", None)
                return self._parse_record(raw, domain=domain, operation="get")
            return None
        except SourceDocumentStoreError:
            raise
        except Exception as exc:
            self._log_failure("get", exc, domain)
            raise SourceDocumentStoreError(
                "The document provenance record could not be read. Check MongoDB connectivity and server diagnostics."
            ) from None

    async def list(
        self,
        domain: ExpertName,
        *,
        limit: int,
        offset: int,
    ) -> list[SourceDocumentRecord]:
        try:
            cursor = (
                self._collection(domain)
                .find({})
                .sort("uploaded_at", -1)
                .skip(offset)
                .limit(limit)
            )
            rows = await cursor.to_list(length=limit)
            records: list[SourceDocumentRecord] = []
            for row in rows:
                row.pop("_id", None)
                records.append(self._parse_record(row, domain=domain, operation="list"))
            return records
        except SourceDocumentStoreError:
            raise
        except Exception as exc:
            self._log_failure("list", exc, domain)
            raise SourceDocumentStoreError(
                "The document provenance records could not be listed. Check MongoDB connectivity and server diagnostics."
            ) from None

    async def update_status(
        self,
        domain: ExpertName,
        document_id: str,
        status: str,
        checked_at: str,
    ) -> None:
        try:
            result = await self._collection(domain).update_one(
                {"document_id": document_id},
                {
                    "$set": {
                        "nugen_status": status,
                        "status_checked_at": checked_at,
                    }
                },
            )
            if result.matched_count == 0:
                logger.warning(
                    "Mongo source-document status update matched no provenance record: domain=%s",
                    domain,
                )
                raise SourceDocumentStoreError(
                    "The document provenance record was not found; its status was not saved."
                )
        except SourceDocumentStoreError:
            raise
        except Exception as exc:
            self._log_failure("update_status", exc, domain)
            raise SourceDocumentStoreError(
                "The document status record could not be updated. Check MongoDB connectivity and server diagnostics."
            ) from None

    async def get_active_source_selection(
        self,
        domain: ExpertName,
    ) -> ActiveSourceSelection | None:
        try:
            raw = await self._active_collection().find_one({"_id": domain})
            if raw is None:
                return None
            raw.pop("_id", None)
            try:
                selection = ActiveSourceSelection.model_validate(raw)
                if selection.domain != domain:
                    raise SourceDocumentStoreError(
                        "The stored active-source selection is associated with a different domain; inference is stopped."
                    )
                return selection
            except ValidationError as exc:
                fields = sorted(
                    ".".join(str(part) for part in item.get("loc", ()))
                    + ":"
                    + str(item.get("type", "validation_error"))
                    for item in exc.errors()
                )
                logger.warning(
                    "Malformed active-source selection: domain=%s error_type=%s fields=%s",
                    domain,
                    type(exc).__name__,
                    fields,
                )
                raise SourceDocumentStoreError(
                    "The stored active-source selection is malformed; inference is stopped."
                ) from None
        except SourceDocumentStoreError:
            raise
        except Exception as exc:
            self._log_failure("get_active_source_selection", exc, domain)
            raise SourceDocumentStoreError(
                "The active-source selection could not be read. Check MongoDB connectivity and server diagnostics."
            ) from None

    async def activate_source_selection(
        self,
        domain: ExpertName,
        document_ids: list[str],
        *,
        expected_revision: int | None,
        changed_at: datetime,
    ) -> ActiveSourceSelection:
        """Atomically switch a whole document set, preserving one rollback set."""

        try:
            collection = self._active_collection()
            current = await self.get_active_source_selection(domain)
            current_revision = current.revision if current is not None else None
            if current_revision != expected_revision:
                raise ActiveSourceConflictError(
                    "The active-source selection changed during validation; refresh and retry."
                )

            previous_ids = current.active_document_ids if current is not None else []
            selection = ActiveSourceSelection(
                domain=domain,
                active_document_ids=document_ids,
                previous_document_ids=previous_ids,
                revision=(expected_revision or 0) + 1,
                changed_at=changed_at,
            )
            row = selection.model_dump(mode="json")
            row["_id"] = domain

            if expected_revision is None:
                await collection.insert_one(row)
            else:
                result = await collection.update_one(
                    {"_id": domain, "revision": expected_revision},
                    {
                        "$set": {
                            "domain": domain,
                            "active_document_ids": document_ids,
                            "previous_document_ids": previous_ids,
                            "revision": expected_revision + 1,
                            "changed_at": changed_at.isoformat(),
                        }
                    },
                )
                if result.matched_count == 0:
                    raise ActiveSourceConflictError(
                        "The active-source selection changed during validation; refresh and retry."
                    )
            return selection
        except (ActiveSourceConflictError, SourceDocumentStoreError):
            raise
        except Exception as exc:
            if type(exc).__name__ == "DuplicateKeyError":
                raise ActiveSourceConflictError(
                    "The active-source selection changed during validation; refresh and retry."
                ) from None
            self._log_failure("activate_source_selection", exc, domain)
            raise SourceDocumentStoreError(
                "The active-source selection could not be saved. Existing source records were preserved."
            ) from None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._database = None
            self._indexes_ready = False
