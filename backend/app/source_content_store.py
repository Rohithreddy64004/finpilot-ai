"""Private local storage for uploaded source text, keyed by domain and Nugen ID."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path

from backend.app.config import Settings, get_settings
from backend.app.schemas import ExpertName
from backend.app.source_documents import SourceDocumentStoreError


MAX_SOURCE_BYTES = 100 * 1024 * 1024
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SourceContentStoreError(SourceDocumentStoreError):
    """An upload's text could not be safely read or persisted for retrieval."""


class LocalSourceContentStore:
    """Atomic filesystem store; it never derives a path from an untrusted ID."""

    def __init__(self, settings: Settings | None = None) -> None:
        configured = Path((settings or get_settings()).source_content_directory).expanduser()
        self._root = (
            configured if configured.is_absolute() else _PROJECT_ROOT / configured
        ).resolve()

    def _path(self, domain: ExpertName, document_id: str) -> Path:
        if domain not in {"tax", "investment", "insurance"}:
            raise SourceContentStoreError("The source document domain is invalid.")
        document_key = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
        target = (self._root / domain / f"{document_key}.txt").resolve()
        if not target.is_relative_to(self._root):
            raise SourceContentStoreError(
                "The source content path is outside the configured storage directory."
            )
        return target

    async def save(self, domain: ExpertName, document_id: str, content: bytes) -> None:
        if len(content) > MAX_SOURCE_BYTES:
            raise SourceContentStoreError(
                "The source text exceeds the configured local storage limit."
            )
        await asyncio.to_thread(self._save_sync, domain, document_id, content)

    def _save_sync(self, domain: ExpertName, document_id: str, content: bytes) -> None:
        target = self._path(domain, document_id)
        temporary_path: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() == content:
                    return
                raise SourceContentStoreError(
                    "Source text already exists for this document ID and was not overwritten."
                )
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=".pending-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            try:
                # Same-directory hard-link creation is atomic and fails if a
                # concurrent writer already created the destination. Unlike
                # replace/rename, it cannot silently overwrite those bytes.
                os.link(temporary_path, target)
            except FileExistsError:
                if target.read_bytes() == content:
                    return
                raise SourceContentStoreError(
                    "Source text already exists for this document ID and was not overwritten."
                ) from None
        except SourceContentStoreError:
            raise
        except OSError:
            raise SourceContentStoreError(
                "Source text could not be saved in the local retrieval store."
            ) from None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    # Cleanup must not replace the original write result/error.
                    pass

    async def get(self, domain: ExpertName, document_id: str) -> bytes | None:
        return await asyncio.to_thread(self._get_sync, domain, document_id)

    def _get_sync(self, domain: ExpertName, document_id: str) -> bytes | None:
        path = self._path(domain, document_id)
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise SourceContentStoreError(
                "Source text could not be read from the local retrieval store."
            ) from None
        if len(content) > MAX_SOURCE_BYTES:
            raise SourceContentStoreError(
                "Stored source text exceeds the local retrieval limit."
            )
        return content


def get_source_content_store() -> LocalSourceContentStore:
    """Dependency factory for the configured source text directory."""

    return LocalSourceContentStore(get_settings())
