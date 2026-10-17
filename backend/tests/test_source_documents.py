from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import pymongo
from backend.app.config import Settings
from backend.app.schemas import SourceDocumentRecord
from backend.app.source_documents import (
    ActiveSourceConflictError,
    MongoSourceDocumentRepository,
    SourceDocumentStoreError,
)


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = list(rows)

    def sort(self, *_args: Any) -> "FakeCursor":
        return self

    def skip(self, count: int) -> "FakeCursor":
        self.rows = self.rows[count:]
        return self

    def limit(self, count: int) -> "FakeCursor":
        self.rows = self.rows[:count]
        return self

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        return self.rows[:length]


class FakeCollection:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.index_calls: list[tuple[Any, bool]] = []

    async def create_index(self, field: str, *, unique: bool) -> str:
        self.index_calls.append((field, unique))
        return f"{field}_1"

    async def insert_one(self, row: dict[str, Any]) -> None:
        if any(
            (
                "_id" in row
                and existing.get("_id") == row.get("_id")
            )
            or (
                "document_id" in row
                and existing.get("document_id") == row.get("document_id")
            )
            for existing in self.rows
        ):
            raise pymongo.errors.DuplicateKeyError("Contains private connection information")
        self.rows.append(dict(row))

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return next(
            (dict(row) for row in self.rows if all(row.get(key) == value for key, value in query.items())),
            None,
        )

    def find(self, _query: dict[str, Any]) -> FakeCursor:
        return FakeCursor([dict(row) for row in self.rows])

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> Any:
        row = next(
            (row for row in self.rows if all(row.get(key) == value for key, value in query.items())),
            None,
        )
        if row is None:
            return SimpleNamespace(matched_count=0)
        row.update(update["$set"])
        return SimpleNamespace(matched_count=1)


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


class FakeAdmin:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls = 0

    async def command(self, _command: dict[str, Any]) -> dict[str, int]:
        self.calls += 1
        if self.error:
            raise self.error
        return {"ok": 1}


class FakeClient:
    def __init__(self) -> None:
        self.database = FakeDatabase()
        self.admin = FakeAdmin()
        self.closed = False

    def __getitem__(self, _name: str) -> FakeDatabase:
        return self.database

    async def close(self) -> None:
        self.closed = True


def record(domain: str, document_id: str = "fixture-document") -> SourceDocumentRecord:
    return SourceDocumentRecord(
        document_id=document_id,
        domain=domain,
        document_name=f"{domain} official source",
        original_filename=f"{domain}.txt",
        source_url={
            "tax": "https://www.incometax.gov.in/iec/foportal/",
            "investment": "https://investor.sebi.gov.in/",
            "insurance": "https://irdai.gov.in/policyholders",
        }[domain],
        retrieved_on=date(2026, 10, 15),
        uploaded_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
        size_bytes=1000,
        nugen_status="READY",
    )


def repository_with_fake_database() -> tuple[MongoSourceDocumentRepository, FakeClient]:
    repository = MongoSourceDocumentRepository(Settings(_env_file=None))
    client = FakeClient()
    repository._client = client
    repository._database = client.database
    return repository, client


def test_mongodb_connection_uses_the_configured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    def create_client(uri: str, **kwargs: Any) -> FakeClient:
        captured.append((uri, kwargs))
        return FakeClient()

    monkeypatch.setattr(pymongo, "AsyncMongoClient", create_client)
    repository = MongoSourceDocumentRepository(
        Settings(
            _env_file=None,
            mongodb_uri="mongodb://unit-user:unit-password@mongo.test/finpilot",
            mongodb_timeout_ms=4_321,
        )
    )

    repository._collection("tax")

    assert captured[0][1] == {
        "serverSelectionTimeoutMS": 4_321,
        "connectTimeoutMS": 4_321,
        "socketTimeoutMS": 4_321,
    }


@pytest.mark.asyncio
async def test_mongo_readiness_logs_safe_exception_class_and_recovers(caplog: pytest.LogCaptureFixture) -> None:
    repository, client = repository_with_fake_database()
    client.admin.error = RuntimeError("mongodb://private-user:do-not-log@private-host")

    with pytest.raises(SourceDocumentStoreError, match="server connectivity"):
        await repository.check_ready()

    assert "RuntimeError" in caplog.text
    assert "do-not-log" not in caplog.text
    assert "private-host" not in caplog.text

    client.admin.error = None
    await repository.check_ready()
    assert client.admin.calls == 2
    assert repository._indexes_ready is True
    assert all(
        client.database[f"finpilot_source_documents_{domain}"].index_calls == [
            ("document_id", True)
        ]
        for domain in ("tax", "investment", "insurance")
    )


@pytest.mark.asyncio
async def test_mongo_provenance_reads_are_domain_scoped_and_status_updates_persist() -> None:
    repository, _ = repository_with_fake_database()
    await repository.check_ready()
    await repository.save(record("tax", "shared-fixture-id"))
    await repository.save(record("investment", "shared-fixture-id"))
    await repository.save(record("insurance", "shared-fixture-id"))

    tax_rows = await repository.list("tax", limit=10, offset=0)
    investment_record = await repository.get("investment", "shared-fixture-id")
    insurance_rows = await repository.list("insurance", limit=10, offset=0)
    assert await repository.get("tax", "missing-id") is None
    await repository.update_status(
        "tax",
        "shared-fixture-id",
        "PROCESSING",
        "2026-10-15T10:00:00+00:00",
    )
    updated = await repository.get("tax", "shared-fixture-id")

    assert [row.domain for row in tax_rows] == ["tax"]
    assert investment_record is not None and investment_record.domain == "investment"
    assert [row.domain for row in insurance_rows] == ["insurance"]
    assert updated is not None and updated.nugen_status == "PROCESSING"


@pytest.mark.asyncio
async def test_duplicate_document_id_cannot_replace_existing_provenance() -> None:
    repository, client = repository_with_fake_database()
    await repository.check_ready()
    original = record("tax", "same-document-id")
    await repository.save(original)
    changed = SourceDocumentRecord.model_validate(
        original.model_dump(mode="json")
        | {
            "document_name": "Different record",
            "source_url": "https://www.incometax.gov.in/different",
        }
    )

    with pytest.raises(SourceDocumentStoreError, match="was not overwritten"):
        await repository.save(changed)

    loaded = await repository.get("tax", "same-document-id")
    assert loaded is not None
    assert loaded.document_name == original.document_name
    assert str(loaded.source_url) == str(original.source_url)
    assert len(client.database["finpilot_source_documents_tax"].rows) == 1


@pytest.mark.asyncio
async def test_malformed_provenance_record_returns_safe_error_and_field_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository, client = repository_with_fake_database()
    client.database["finpilot_source_documents_tax"].rows.append(
        {"document_id": "do-not-log-this-record", "unexpected_private_field": "secret value"}
    )

    with pytest.raises(SourceDocumentStoreError, match="record is malformed"):
        await repository.list("tax", limit=10, offset=0)

    assert "document_name" in caplog.text
    assert "do-not-log-this-record" not in caplog.text
    assert "secret value" not in caplog.text


@pytest.mark.asyncio
async def test_status_update_missing_record_is_not_reported_as_success() -> None:
    repository, _ = repository_with_fake_database()

    with pytest.raises(SourceDocumentStoreError, match="not found"):
        await repository.update_status(
            "insurance",
            "missing-id",
            "READY",
            "2026-10-15T10:00:00+00:00",
        )


@pytest.mark.asyncio
async def test_active_source_switch_is_atomic_and_keeps_previous_set_for_rollback() -> None:
    repository, client = repository_with_fake_database()
    await repository.check_ready()
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)

    original = await repository.activate_source_selection(
        "tax",
        ["old-tax-document"],
        expected_revision=None,
        changed_at=now,
    )
    updated = await repository.activate_source_selection(
        "tax",
        ["new-tax-document"],
        expected_revision=original.revision,
        changed_at=now,
    )
    rollback = await repository.activate_source_selection(
        "tax",
        updated.previous_document_ids,
        expected_revision=updated.revision,
        changed_at=now,
    )

    assert original.active_document_ids == ["old-tax-document"]
    assert original.previous_document_ids == []
    assert updated.active_document_ids == ["new-tax-document"]
    assert updated.previous_document_ids == ["old-tax-document"]
    assert rollback.active_document_ids == ["old-tax-document"]
    assert rollback.previous_document_ids == ["new-tax-document"]
    persisted = await repository.get_active_source_selection("tax")
    assert persisted == rollback
    assert len(client.database["finpilot_active_source_selections"].rows) == 1


@pytest.mark.asyncio
async def test_active_source_switch_rejects_stale_revision() -> None:
    repository, _ = repository_with_fake_database()
    await repository.check_ready()
    current = await repository.activate_source_selection(
        "tax",
        ["tax-document-a"],
        expected_revision=None,
        changed_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
    )
    await repository.activate_source_selection(
        "tax",
        ["tax-document-b"],
        expected_revision=current.revision,
        changed_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
    )

    with pytest.raises(ActiveSourceConflictError, match="changed during validation"):
        await repository.activate_source_selection(
            "tax",
            ["tax-document-c"],
            expected_revision=current.revision,
            changed_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_malformed_active_selection_fails_closed_and_logs_no_document_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository, client = repository_with_fake_database()
    client.database["finpilot_active_source_selections"].rows.append(
        {
            "_id": "tax",
            "domain": "tax",
            "active_document_ids": ["private-doc-id", "private-doc-id"],
            "previous_document_ids": [],
            "revision": 1,
            "changed_at": datetime(2026, 10, 15, tzinfo=timezone.utc).isoformat(),
        }
    )

    with pytest.raises(SourceDocumentStoreError, match="selection is malformed"):
        await repository.get_active_source_selection("tax")

    assert "private-doc-id" not in caplog.text
