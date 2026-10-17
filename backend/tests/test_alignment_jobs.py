from datetime import datetime, timezone

import pytest

from backend.app.alignment_jobs import MongoAlignmentJobRepository
from backend.app.config import Settings
from backend.app.schemas import AlignmentJobRecord


class FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.sort_value: tuple[str, int] | None = None
        self.limit_value: int | None = None

    def sort(self, field: str, direction: int):
        self.sort_value = (field, direction)
        return self

    def limit(self, count: int):
        self.limit_value = count
        return self

    async def to_list(self, *, length: int):
        assert length == self.limit_value
        return self.rows[:length]


class FakeCollection:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.query: dict | None = None
        self.cursor = FakeCursor(rows)

    def find(self, query: dict):
        self.query = query
        return self.cursor


def alignment_row() -> dict:
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    return AlignmentJobRecord(
        tracking_id="tracking-tax",
        domain="tax",
        alignment_name="Tax education",
        base_model_id="base-model",
        document_ids=["tax-document"],
        submission_state="SUBMITTED",
        status_note="Provider status has not been refreshed.",
        created_at=now,
        updated_at=now,
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_alignment_repository_lists_only_requested_domain_and_bounds_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = alignment_row()
    collection = FakeCollection([row])
    repository = MongoAlignmentJobRepository(Settings(_env_file=None))
    monkeypatch.setattr(repository, "_collection", lambda: collection)

    rows = await repository.list_by_domain("tax", limit=1)

    assert collection.query == {"domain": "tax"}
    assert collection.cursor.sort_value == ("updated_at", -1)
    assert collection.cursor.limit_value == 1
    assert len(rows) == 1
    assert rows[0].domain == "tax"
    assert rows[0].alignment_status == "unaligned"
    assert rows[0].inference_ready is False
