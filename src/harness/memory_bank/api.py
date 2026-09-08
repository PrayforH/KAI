from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from harness.api.dependencies import (
    ApiContainer,
    Identity,
    ensure_permission,
    get_container,
    require_identity,
)
from harness.memory_bank.models import (
    MemoryConsent,
    MemoryEntry,
    MemoryRetention,
    MemorySearchHit,
    MemoryVersionRequest,
    ProposeMemoryRequest,
    ReplaceConsentRequest,
    ReplaceRetentionRequest,
    SearchMemoryRequest,
    UpdateMemoryRequest,
)
from harness.memory_bank.scope import policy_key

router = APIRouter(prefix="/memory-bank", tags=["memory-bank"])


@router.get("/entries", response_model=list[MemoryEntry])
async def list_entries(
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    agent_name: Annotated[str | None, Query(alias="agentName")] = None,
) -> list[MemoryEntry]:
    ensure_permission(identity, "tasks:read")
    return list(
        await container.memory_bank.list_entries(
            identity.tenant_id, identity.user_id, agent_name=agent_name
        )
    )


@router.post("/proposals", response_model=MemoryEntry, status_code=201)
async def propose(
    body: ProposeMemoryRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> MemoryEntry:
    ensure_permission(identity, "tasks:write")
    return await container.memory_bank.propose(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        agent_name=body.agent_name,
        owner_id=body.agent_owner_user_id or identity.user_id,
        content=body.content,
        memory_type=body.memory_type,
        topic=body.topic,
        conditions=body.conditions,
        source_kind=body.source_kind,
        source_label=body.source_label,
        confidence=body.confidence,
        run_id=body.run_id,
        session_id=body.session_id,
    )


@router.post("/entries/{entry_id}/confirm", response_model=MemoryEntry)
async def confirm(
    entry_id: str,
    body: MemoryVersionRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> MemoryEntry:
    ensure_permission(identity, "tasks:write")
    return await container.memory_bank.confirm(
        identity.tenant_id, identity.user_id, entry_id, body.expected_version
    )


@router.post("/entries/{entry_id}/reject", response_model=MemoryEntry)
async def reject(
    entry_id: str,
    body: MemoryVersionRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> MemoryEntry:
    ensure_permission(identity, "tasks:write")
    return await container.memory_bank.reject(
        identity.tenant_id, identity.user_id, entry_id, body.expected_version
    )


@router.put("/entries/{entry_id}", response_model=MemoryEntry)
async def update(
    entry_id: str,
    body: UpdateMemoryRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> MemoryEntry:
    ensure_permission(identity, "tasks:write")
    return await container.memory_bank.update(
        identity.tenant_id,
        identity.user_id,
        entry_id,
        expected_version=body.expected_version,
        content=body.content,
        confidence=body.confidence,
    )


@router.delete("/entries/{entry_id}", status_code=204)
async def delete_entry(
    entry_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    expected_version: Annotated[int, Query(alias="expectedVersion", ge=1)],
) -> Response:
    ensure_permission(identity, "tasks:write")
    await container.memory_bank.delete(
        identity.tenant_id, identity.user_id, entry_id, expected_version
    )
    return Response(status_code=204)


@router.post("/search", response_model=list[MemorySearchHit])
async def search(
    body: SearchMemoryRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> list[MemorySearchHit]:
    ensure_permission(identity, "tasks:read")
    return list(
        await container.memory_bank.search(
            identity.tenant_id,
            identity.user_id,
            body.agent_name,
            body.query,
            limit=body.limit,
            owner_id=body.agent_owner_user_id or identity.user_id,
        )
    )


@router.get("/export", response_model=dict[str, object])
async def export_user(
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    response: Response,
) -> dict[str, object]:
    ensure_permission(identity, "tasks:read")
    response.headers["Content-Disposition"] = 'attachment; filename="harness-memory-export.json"'
    response.headers["Cache-Control"] = "private, no-store"
    return await container.memory_bank.export_user(identity.tenant_id, identity.user_id)


@router.get(
    "/agents/{agent_name}/policy",
    response_model=dict[str, MemoryConsent | MemoryRetention | None],
)
async def policy(
    agent_name: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    owner_id: Annotated[str | None, Query(alias="agentOwnerUserId")] = None,
) -> dict[str, MemoryConsent | MemoryRetention | None]:
    ensure_permission(identity, "tasks:read")
    consent, retention = await container.memory_bank.get_policy(
        identity.tenant_id, identity.user_id, policy_key(identity.user_id, agent_name, owner_id)
    )
    return {"consent": consent, "retention": retention}


@router.put("/agents/{agent_name}/consent", response_model=MemoryConsent)
async def replace_consent(
    agent_name: str,
    body: ReplaceConsentRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    owner_id: Annotated[str | None, Query(alias="agentOwnerUserId")] = None,
) -> MemoryConsent:
    ensure_permission(identity, "tasks:write")
    return await container.memory_bank.replace_consent(
        identity.tenant_id,
        identity.user_id,
        policy_key(identity.user_id, agent_name, owner_id),
        expected_version=body.expected_version,
        allow_agent_personal=body.allow_agent_personal,
    )


@router.put("/agents/{agent_name}/retention", response_model=MemoryRetention)
async def replace_retention(
    agent_name: str,
    body: ReplaceRetentionRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    owner_id: Annotated[str | None, Query(alias="agentOwnerUserId")] = None,
) -> MemoryRetention:
    ensure_permission(identity, "tasks:write")
    return await container.memory_bank.replace_retention(
        identity.tenant_id,
        identity.user_id,
        policy_key(identity.user_id, agent_name, owner_id),
        expected_version=body.expected_version,
        default_days=body.default_days,
        max_days=body.max_days,
    )
