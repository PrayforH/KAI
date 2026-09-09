from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import PlainTextResponse

from harness.knowledge.models import (
    AddKnowledgeMembersRequest,
    AddKnowledgeMembersResult,
    CreateKnowledgeBaseRequest,
    CreateKnowledgeDocumentRequest,
    CreateKnowledgeSourceRequest,
    CreateKnowledgeSourceResult,
    KnowledgeBase,
    KnowledgeBaseMember,
    KnowledgeDocumentChunk,
    KnowledgeDocumentStatus,
    KnowledgeDocumentTable,
    KnowledgeSnapshot,
    KnowledgeSourceSummary,
    KnowledgeSyncRun,
    KnowledgeWikiGraph,
    KnowledgeWikiPage,
    KnowledgeWikiStats,
    ReplaceKnowledgeBaseRequest,
    ReplaceKnowledgeSourceRequest,
    SearchKnowledgeRequest,
    SearchKnowledgeResponse,
    UpdateKnowledgeMemberRequest,
)
from harness.knowledge.ports import (
    KnowledgeEngineError,
    KnowledgeEngineNotConfiguredError,
)
from harness.knowledge.service import KnowledgeService
from harness.studio.api import (
    StudioActor,
    require_studio_reader,
    require_studio_writer,
)

router = APIRouter(prefix="/v1/studio/knowledge", tags=["knowledge"])


def get_knowledge_service(request: Request) -> KnowledgeService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "knowledge", None)
    if not isinstance(service, KnowledgeService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "knowledge_not_configured",
                "message": "Knowledge control plane is not configured",
            },
        )
    return service


async def _translate_engine_error(error: KnowledgeEngineError) -> HTTPException:
    if isinstance(error, KnowledgeEngineNotConfiguredError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "knowledge_engine_not_configured",
                "message": str(error),
            },
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "code": "knowledge_engine_error",
            "message": str(error),
        },
    )


@router.get("/bases", response_model=list[KnowledgeBase])
async def list_knowledge_bases(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> list[KnowledgeBase]:
    return list(await service.list_bases(actor.tenant_id, actor.user_id))


@router.post(
    "/bases",
    response_model=KnowledgeBase,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_base(
    body: CreateKnowledgeBaseRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeBase:
    return await service.create_base(actor.tenant_id, actor.user_id, body)


@router.get("/bases/{reference}", response_model=KnowledgeBase)
async def get_knowledge_base(
    reference: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeBase:
    return await service.get_base(actor.tenant_id, reference, actor.user_id)


@router.put("/bases/{reference}", response_model=KnowledgeBase)
async def replace_knowledge_base(
    reference: str,
    body: ReplaceKnowledgeBaseRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeBase:
    return await service.replace_base(
        actor.tenant_id,
        actor.user_id,
        reference,
        body,
    )


@router.delete(
    "/bases/{reference}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_knowledge_base(
    reference: str,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> None:
    try:
        await service.delete_base(actor.tenant_id, actor.user_id, reference)
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


@router.get("/sources", response_model=list[KnowledgeSourceSummary])
async def list_knowledge_sources(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> list[KnowledgeSourceSummary]:
    return [
        KnowledgeSourceSummary.from_source(source)
        for source in await service.list_sources(actor.tenant_id, actor.user_id)
    ]


@router.post(
    "/sources",
    response_model=CreateKnowledgeSourceResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_source(
    body: CreateKnowledgeSourceRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> CreateKnowledgeSourceResult:
    source, sync = await service.create_source(
        actor.tenant_id,
        actor.user_id,
        body,
    )
    return CreateKnowledgeSourceResult(
        source=KnowledgeSourceSummary.from_source(source),
        sync=sync,
    )


@router.get("/sources/{reference}", response_model=KnowledgeSourceSummary)
async def get_knowledge_source(
    reference: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeSourceSummary:
    return KnowledgeSourceSummary.from_source(
        await service.get_source(actor.tenant_id, reference, actor.user_id)
    )


@router.put("/sources/{reference}", response_model=KnowledgeSourceSummary)
async def replace_knowledge_source(
    reference: str,
    body: ReplaceKnowledgeSourceRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeSourceSummary:
    return KnowledgeSourceSummary.from_source(
        await service.replace_source(
            actor.tenant_id,
            actor.user_id,
            reference,
            body,
        )
    )


@router.post("/sources/{reference}/sync", response_model=KnowledgeSyncRun)
async def sync_knowledge_source(
    reference: str,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeSyncRun:
    return await service.sync_source(actor.tenant_id, actor.user_id, reference)


@router.get("/syncs", response_model=list[KnowledgeSyncRun])
async def list_knowledge_syncs(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    source_reference: str | None = None,
    limit: int = 100,
) -> list[KnowledgeSyncRun]:
    return list(
        await service.list_syncs(
            actor.tenant_id,
            actor.user_id,
            source_reference=source_reference,
            limit=max(1, min(limit, 200)),
        )
    )


@router.get("/snapshots", response_model=list[KnowledgeSnapshot])
async def list_knowledge_snapshots(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    source_reference: str | None = None,
    limit: int = 100,
) -> list[KnowledgeSnapshot]:
    return list(
        await service.list_snapshots(
            actor.tenant_id,
            actor.user_id,
            source_reference=source_reference,
            limit=max(1, min(limit, 200)),
        )
    )


@router.get(
    "/citations/{snapshot_id}/{chunk_id}",
    response_class=PlainTextResponse,
)
async def open_knowledge_citation(
    snapshot_id: str,
    chunk_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> PlainTextResponse:
    chunk = await service.get_visible_chunk(
        actor.tenant_id,
        actor.user_id,
        snapshot_id,
        chunk_id,
    )
    filename = quote(f"{chunk.title}.txt", safe="")
    return PlainTextResponse(
        chunk.content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{filename}",
            "X-Knowledge-Source": chunk.source_reference,
            "X-Knowledge-Document": chunk.document_id,
        },
    )


@router.post("/search", response_model=SearchKnowledgeResponse)
async def search_knowledge(
    body: SearchKnowledgeRequest,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> SearchKnowledgeResponse:
    return await service.search(
        actor.tenant_id,
        actor.user_id,
        body.query,
        knowledge_base_references=body.knowledge_base_references,
        limit=body.limit,
    )


# --- engine-backed document and chunk proxies (WeKnora) -------------------


@router.get(
    "/sources/{reference}/documents",
    response_model=list[KnowledgeDocumentStatus],
)
async def list_source_documents(
    reference: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> list[KnowledgeDocumentStatus]:
    try:
        return await service.list_source_documents(actor.tenant_id, actor.user_id, reference)
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


@router.post(
    "/sources/{reference}/documents",
    response_model=KnowledgeDocumentStatus,
    status_code=status.HTTP_201_CREATED,
)
async def create_source_document(
    reference: str,
    body: CreateKnowledgeDocumentRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeDocumentStatus:
    try:
        return await service.create_source_document(
            actor.tenant_id,
            actor.user_id,
            reference,
            body.title,
            body.content,
        )
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


@router.post(
    "/sources/{reference}/documents/upload",
    response_model=KnowledgeDocumentStatus,
    status_code=status.HTTP_201_CREATED,
)
async def upload_source_document(
    reference: str,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    file: Annotated[UploadFile, File()],
) -> KnowledgeDocumentStatus:
    filename = file.filename or "upload.bin"
    content = await file.read()
    try:
        return await service.upload_source_document(
            actor.tenant_id,
            actor.user_id,
            reference,
            filename,
            content,
        )
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


@router.get(
    "/sources/{reference}/documents/{document_id}",
    response_model=KnowledgeDocumentStatus,
)
async def get_source_document(
    reference: str,
    document_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeDocumentStatus:
    try:
        return await service.get_source_document(
            actor.tenant_id,
            actor.user_id,
            reference,
            document_id,
        )
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


@router.delete(
    "/sources/{reference}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_source_document(
    reference: str,
    document_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> None:
    try:
        await service.delete_source_document(
            actor.tenant_id,
            actor.user_id,
            reference,
            document_id,
        )
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


@router.post(
    "/sources/{reference}/documents/{document_id}/reparse",
    response_model=KnowledgeDocumentStatus,
)
async def reparse_source_document(
    reference: str,
    document_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeDocumentStatus:
    try:
        await service.reparse_source_document(
            actor.tenant_id,
            actor.user_id,
            reference,
            document_id,
        )
        return await service.get_source_document(
            actor.tenant_id,
            actor.user_id,
            reference,
            document_id,
        )
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


@router.get(
    "/sources/{reference}/documents/{document_id}/table",
    response_model=KnowledgeDocumentTable,
)
async def get_source_document_table(
    reference: str,
    document_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeDocumentTable:
    try:
        return await service.get_source_document_table(
            actor.tenant_id,
            actor.user_id,
            reference,
            document_id,
        )
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


@router.get(
    "/sources/{reference}/documents/{document_id}/chunks",
    response_model=list[KnowledgeDocumentChunk],
)
async def list_source_document_chunks(
    reference: str,
    document_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> list[KnowledgeDocumentChunk]:
    try:
        return await service.list_source_chunks(
            actor.tenant_id,
            actor.user_id,
            reference,
            document_id,
        )
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


@router.get(
    "/sources/{reference}/chunks/{chunk_id}",
    response_model=KnowledgeDocumentChunk,
)
async def get_source_chunk(
    reference: str,
    chunk_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeDocumentChunk:
    try:
        return await service.get_source_chunk(
            actor.tenant_id,
            actor.user_id,
            reference,
            chunk_id,
        )
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


# --- wiki proxies (engine-backed bases) ------------------------------------


@router.get(
    "/sources/{reference}/wiki/pages",
    response_model=list[KnowledgeWikiPage],
)
async def list_wiki_pages(
    reference: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> list[KnowledgeWikiPage]:
    try:
        return await service.list_wiki_pages(actor.tenant_id, actor.user_id, reference)
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


@router.get(
    "/sources/{reference}/wiki/pages/{slug:path}",
    response_model=KnowledgeWikiPage,
)
async def get_wiki_page(
    reference: str,
    slug: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeWikiPage:
    try:
        return await service.get_wiki_page(actor.tenant_id, actor.user_id, reference, slug)
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


@router.get(
    "/sources/{reference}/wiki/search",
    response_model=list[KnowledgeWikiPage],
)
async def search_wiki_pages(
    reference: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    q: str = "",
    limit: int = 20,
) -> list[KnowledgeWikiPage]:
    query = (q or "").strip()
    if not query:
        return []
    try:
        return await service.search_wiki_pages(
            actor.tenant_id,
            actor.user_id,
            reference,
            query,
            limit=limit,
        )
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


@router.get(
    "/sources/{reference}/wiki/graph",
    response_model=KnowledgeWikiGraph,
)
async def get_wiki_graph(
    reference: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeWikiGraph:
    try:
        return await service.wiki_graph(actor.tenant_id, actor.user_id, reference)
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


@router.get(
    "/sources/{reference}/wiki/stats",
    response_model=KnowledgeWikiStats,
)
async def get_wiki_stats(
    reference: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeWikiStats:
    try:
        return await service.wiki_stats(actor.tenant_id, actor.user_id, reference)
    except KnowledgeEngineError as error:
        raise await _translate_engine_error(error) from error


# --- knowledge base membership (phase 1: per-user viewer/editor) -----------


@router.get(
    "/bases/{reference}/members",
    response_model=list[KnowledgeBaseMember],
)
async def list_knowledge_base_members(
    reference: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> list[KnowledgeBaseMember]:
    return await service.list_members(actor.tenant_id, actor.user_id, reference)


@router.post(
    "/bases/{reference}/members",
    response_model=AddKnowledgeMembersResult,
    status_code=status.HTTP_201_CREATED,
)
async def add_knowledge_base_members(
    reference: str,
    body: AddKnowledgeMembersRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> AddKnowledgeMembersResult:
    return await service.add_members(actor.tenant_id, actor.user_id, reference, body)


@router.put(
    "/bases/{reference}/members/{member_id}",
    response_model=KnowledgeBaseMember,
)
async def update_knowledge_base_member(
    reference: str,
    member_id: str,
    body: UpdateKnowledgeMemberRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeBaseMember:
    return await service.update_member_role(
        actor.tenant_id,
        actor.user_id,
        reference,
        member_id,
        body.role,
    )


@router.delete(
    "/bases/{reference}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_knowledge_base_member(
    reference: str,
    member_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> None:
    await service.remove_member(actor.tenant_id, actor.user_id, reference, member_id)


@router.get("/directory/users")
async def search_directory_users(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    q: str = "",
    limit: int = 20,
) -> list[dict[str, str]]:
    return await service.search_directory_users(
        actor.tenant_id,
        actor.user_id,
        q,
        limit=limit,
    )
