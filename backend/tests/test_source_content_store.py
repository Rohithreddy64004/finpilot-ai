import asyncio
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.source_content_store import (
    LocalSourceContentStore,
    SourceContentStoreError,
)


@pytest.mark.asyncio
async def test_local_content_store_is_domain_scoped_and_does_not_overwrite(
    tmp_path: Path,
) -> None:
    store = LocalSourceContentStore(
        Settings(_env_file=None, source_content_directory=str(tmp_path))
    )
    await store.save("tax", "document/with/path", b"retrievable source text")

    assert await store.get("tax", "document/with/path") == b"retrievable source text"
    assert await store.get("investment", "document/with/path") is None

    with pytest.raises(SourceContentStoreError, match="was not overwritten"):
        await store.save("tax", "document/with/path", b"different text")


@pytest.mark.asyncio
async def test_concurrent_saves_cannot_replace_existing_source_bytes(tmp_path: Path) -> None:
    store = LocalSourceContentStore(
        Settings(_env_file=None, source_content_directory=str(tmp_path))
    )
    results = await asyncio.gather(
        store.save("tax", "same-id", b"first content"),
        store.save("tax", "same-id", b"second content"),
        return_exceptions=True,
    )

    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, SourceContentStoreError) for result in results) == 1
    assert await store.get("tax", "same-id") in {
        b"first content",
        b"second content",
    }
