"""Explicit document and alignment workflow operations for Nugen."""

from backend.app.nugen.client import NugenClient, NugenUploadFile
from backend.app.nugen.schemas import (
    DeploymentStatusResponse,
    DeploymentStatusValue,
    AlignedModelsResponse,
    AlignmentStatusResponse,
    BaseModelsResponse,
    CreateAlignmentRequest,
    CreateAlignmentResponse,
    DocumentStatusResponse,
    DocumentStatusValue,
    DocumentsResponse,
    UploadedDocumentsResponse,
)


class NugenAlignmentService:
    """Typed workflow facade; no work starts unless a method is called."""

    def __init__(self, client: NugenClient) -> None:
        self._client = client

    async def base_models(
        self,
        *,
        alignment_ready: bool | None = None,
        model_type: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> BaseModelsResponse:
        return await self._client.list_base_models(
            alignment_ready=alignment_ready,
            model_type=model_type,
            limit=limit,
            offset=offset,
        )

    async def upload_documents(
        self,
        files: list[NugenUploadFile],
        *,
        categories: list[str] | None = None,
        names: list[str] | None = None,
    ) -> UploadedDocumentsResponse:
        return await self._client.upload_documents(
            files,
            categories=categories,
            names=names,
        )

    async def list_documents(
        self,
        *,
        status: DocumentStatusValue | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> DocumentsResponse:
        return await self._client.list_documents(
            status=status,
            limit=limit,
            offset=offset,
        )

    async def document_status(self, document_id: str) -> DocumentStatusResponse:
        return await self._client.get_document_status(document_id)

    async def create_alignment(
        self,
        request: CreateAlignmentRequest,
    ) -> CreateAlignmentResponse:
        """Submit one asynchronous, potentially costly alignment job."""

        return await self._client.create_alignment(request)

    async def alignment_status(self, alignment_id: str) -> AlignmentStatusResponse:
        return await self._client.get_alignment_status(alignment_id)

    async def aligned_models(
        self,
        *,
        deployment_status: DeploymentStatusValue | None = None,
        base_model_id: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> AlignedModelsResponse:
        return await self._client.list_aligned_models(
            deployment_status=deployment_status,
            base_model_id=base_model_id,
            limit=limit,
            offset=offset,
        )

    async def deploy_model(self, model_id: str, *, early: bool = False) -> str:
        """Explicit deployment call; never invoked by alignment submission or polling."""

        return await self._client.deploy_aligned_model(model_id, early=early)

    async def deployment_status(self, model_id: str) -> DeploymentStatusResponse:
        return await self._client.get_deployment_status(model_id)
