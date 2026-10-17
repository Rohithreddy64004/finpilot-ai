"""Persistence for queries, user feedback, and separately reviewed corrections."""

from __future__ import annotations

from typing import Protocol

from fastapi import Request
from pydantic import ValidationError

from backend.app.config import Settings, get_settings
from backend.app.schemas import (
    FeedbackRecord,
    FeedbackSummaryResponse,
    QueryLogRecord,
    VerifiedCorrectionRecord,
)


class InteractionStoreError(RuntimeError):
    """Safe persistence failure for the interaction collections."""


class InteractionRepository(Protocol):
    async def save_query(self, record: QueryLogRecord) -> None: ...

    async def get_query(self, query_id: str) -> QueryLogRecord | None: ...

    async def save_feedback(self, record: FeedbackRecord) -> None: ...

    async def feedback_summary(self) -> FeedbackSummaryResponse: ...

    async def save_verified_correction(self, record: VerifiedCorrectionRecord) -> None: ...

    async def count_verified_corrections(self) -> int: ...


class MongoInteractionRepository:
    """Async Mongo repository with physically separate interaction collections."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = None
        self._database = None

    def _collection(self, name: str):
        if name not in {"queries", "feedback", "verified_corrections"}:
            raise ValueError("Unknown interaction collection.")
        if self._database is None:
            try:
                from pymongo import AsyncMongoClient

                self._client = AsyncMongoClient(
                    self._settings.mongodb_uri.get_secret_value(),
                    serverSelectionTimeoutMS=self._settings.mongodb_timeout_ms,
                    connectTimeoutMS=self._settings.mongodb_timeout_ms,
                    socketTimeoutMS=self._settings.mongodb_timeout_ms,
                )
                self._database = self._client[self._settings.mongodb_database]
            except Exception:
                raise InteractionStoreError(
                    "The interaction database could not be initialized."
                ) from None
        return self._database[f"finpilot_{name}"]

    @staticmethod
    def _parse_query(raw: dict) -> QueryLogRecord:
        raw.pop("_id", None)
        try:
            return QueryLogRecord.model_validate(raw)
        except ValidationError:
            raise InteractionStoreError(
                "A stored query record is malformed; no interaction was changed."
            ) from None

    async def save_query(self, record: QueryLogRecord) -> None:
        try:
            await self._collection("queries").insert_one(record.model_dump(mode="json"))
        except InteractionStoreError:
            raise
        except Exception:
            raise InteractionStoreError("The query record could not be saved.") from None

    async def get_query(self, query_id: str) -> QueryLogRecord | None:
        try:
            raw = await self._collection("queries").find_one({"id": query_id})
            return self._parse_query(raw) if raw is not None else None
        except InteractionStoreError:
            raise
        except Exception:
            raise InteractionStoreError("The query record could not be read.") from None

    async def save_feedback(self, record: FeedbackRecord) -> None:
        try:
            await self._collection("feedback").insert_one(record.model_dump(mode="json"))
        except InteractionStoreError:
            raise
        except Exception:
            raise InteractionStoreError("The feedback record could not be saved.") from None

    async def feedback_summary(self) -> FeedbackSummaryResponse:
        try:
            feedback = self._collection("feedback")
            counts = await feedback.aggregate(
                [
                    {
                        "$group": {
                            "_id": None,
                            "total": {"$sum": 1},
                            "thumbs_up": {
                                "$sum": {"$cond": [{"$eq": ["$rating", "up"]}, 1, 0]}
                            },
                            "thumbs_down": {
                                "$sum": {"$cond": [{"$eq": ["$rating", "down"]}, 1, 0]}
                            },
                        }
                    }
                ]
            ).to_list(length=1)
            expert_rows = await feedback.aggregate(
                [
                    {"$unwind": "$correct_experts"},
                    {"$group": {"_id": "$correct_experts", "count": {"$sum": 1}}},
                ]
            ).to_list(length=3)
            correction_count = await self._collection("verified_corrections").count_documents({})
            values = counts[0] if counts else {}
            expert_counts = {
                row["_id"]: row["count"]
                for row in expert_rows
                if row.get("_id") in {"tax", "investment", "insurance"}
            }
            return FeedbackSummaryResponse(
                total=values.get("total", 0),
                thumbs_up=values.get("thumbs_up", 0),
                thumbs_down=values.get("thumbs_down", 0),
                correct_expert_counts=expert_counts,
                verified_correction_count=correction_count,
            )
        except Exception:
            raise InteractionStoreError("Feedback summary could not be calculated.") from None

    async def save_verified_correction(self, record: VerifiedCorrectionRecord) -> None:
        try:
            await self._collection("verified_corrections").insert_one(
                record.model_dump(mode="json")
            )
        except InteractionStoreError:
            raise
        except Exception:
            raise InteractionStoreError(
                "The reviewed correction could not be saved."
            ) from None

    async def count_verified_corrections(self) -> int:
        try:
            return await self._collection("verified_corrections").count_documents({})
        except Exception:
            raise InteractionStoreError(
                "The reviewed correction count could not be read."
            ) from None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._database = None


def get_interaction_repository(request: Request) -> InteractionRepository:
    repository = getattr(request.app.state, "interaction_repository", None)
    if repository is None:
        repository = MongoInteractionRepository(get_settings())
        request.app.state.interaction_repository = repository
    return repository
