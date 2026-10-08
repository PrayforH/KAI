"""Authenticated, stateless dictation endpoints for web composers."""

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from harness.api.dependencies import (
    ApiContainer,
    Identity,
    ensure_permission,
    get_container,
    require_identity,
)
from harness.core.errors import HarnessDomainError
from harness.dictation.realtime import RealtimeDictation, RealtimeSession
from harness.dictation.service import (
    DictationError,
    DictationService,
    RefineResult,
    VadResult,
)

router = APIRouter(prefix="/dictation", tags=["dictation"])


def service(request: Request) -> DictationService:
    adapter: DictationService = request.app.state.dictation
    if not adapter.settings.enabled:
        raise HTTPException(503, "语音输入尚未启用")
    return adapter


async def audio_bytes(file: UploadFile, adapter: DictationService) -> bytes:
    maximum = adapter.settings.max_audio_seconds * 32_000 + 4096
    content = await file.read(maximum + 1)
    if len(content) > maximum:
        raise HTTPException(413, "录音片段过长")
    return content


@router.get("/capabilities")
async def capabilities(
    request: Request, identity: Annotated[Identity, Depends(require_identity)]
) -> dict[str, object]:
    ensure_permission(identity, "tasks:write")
    adapter: DictationService = request.app.state.dictation
    return {
        "enabled": adapter.settings.enabled,
        "mode": (
            "realtime" if adapter.settings.engine == "funasr_realtime" else "utterance_incremental"
        ),
        "sampleRate": 16_000,
        "maxAudioSeconds": adapter.settings.max_audio_seconds,
        "maxSegmentSeconds": min(12, adapter.settings.max_audio_seconds),
        "silenceMs": adapter.settings.silence_ms,
        "maxSessionSeconds": adapter.settings.max_session_seconds,
    }


@router.post("/vad", response_model=VadResult)
async def detect(
    file: Annotated[UploadFile, File()],
    identity: Annotated[Identity, Depends(require_identity)],
    adapter: Annotated[DictationService, Depends(service)],
) -> VadResult:
    ensure_permission(identity, "tasks:write")
    try:
        return await adapter.detect(await audio_bytes(file, adapter))
    except DictationError as error:
        raise HTTPException(502, str(error)) from error


@router.post("/transcribe")
async def transcribe(
    file: Annotated[UploadFile, File()],
    identity: Annotated[Identity, Depends(require_identity)],
    adapter: Annotated[DictationService, Depends(service)],
) -> dict[str, str]:
    ensure_permission(identity, "tasks:write")
    try:
        return {"text": await adapter.transcribe(await audio_bytes(file, adapter))}
    except DictationError as error:
        raise HTTPException(502, str(error)) from error


class RefineRequest(BaseModel):
    draft: str = Field(min_length=1, max_length=12_000)
    model_route: str = Field(default="", max_length=100)


@router.post("/refine", response_model=RefineResult)
async def refine(
    body: RefineRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    adapter: Annotated[DictationService, Depends(service)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> RefineResult:
    ensure_permission(identity, "tasks:write")
    route = adapter.settings.refine_model_route or body.model_route
    if not route:
        return RefineResult(text=body.draft, draft=body.draft, status="fallback")

    async def complete(system: str, user: str) -> str:
        return await container.model_configurations.complete_text(
            identity.tenant_id,
            route,
            system_prompt=system,
            user_prompt=user,
            max_tokens=4096,
        )

    try:
        return await adapter.refine(body.draft, complete)
    except HarnessDomainError:
        return RefineResult(text=body.draft, draft=body.draft, status="fallback")


def realtime(request: Request) -> RealtimeDictation:
    adapter = service(request)
    if adapter.settings.engine != "funasr_realtime":
        raise HTTPException(503, "实时语音未启用")
    return request.app.state.dictation_realtime


def owned_session(
    manager: RealtimeDictation,
    session_id: str,
    identity: Identity,
) -> RealtimeSession:
    ensure_permission(identity, "tasks:write")
    try:
        return manager.get(session_id, (identity.tenant_id, identity.user_id))
    except DictationError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/sessions", status_code=201)
async def start_session(
    identity: Annotated[Identity, Depends(require_identity)],
    manager: Annotated[RealtimeDictation, Depends(realtime)],
) -> dict[str, str]:
    ensure_permission(identity, "tasks:write")
    try:
        session = await manager.start((identity.tenant_id, identity.user_id))
        return {"id": session.session_id}
    except DictationError as error:
        raise HTTPException(503, str(error)) from error


@router.post("/sessions/{session_id}/audio")
async def send_audio(
    session_id: str,
    request: Request,
    sequence: Annotated[int, Query(ge=0)],
    identity: Annotated[Identity, Depends(require_identity)],
    manager: Annotated[RealtimeDictation, Depends(realtime)],
) -> dict[str, int]:
    session = owned_session(manager, session_id, identity)
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 32_000:
            raise HTTPException(413, "录音片段过长")
    try:
        await manager.send(session, sequence, bytes(content))
        return {"sequence": sequence}
    except DictationError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/sessions/{session_id}/events")
async def session_events(
    session_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(require_identity)],
    manager: Annotated[RealtimeDictation, Depends(realtime)],
) -> StreamingResponse:
    session = owned_session(manager, session_id, identity)
    if session.subscribed:
        raise HTTPException(409, "录音会话已有接收端")
    session.subscribed = True

    async def events():
        try:
            yield 'data: {"type":"ready"}\n\n'
            while not await request.is_disconnected():
                try:
                    item = await asyncio.wait_for(session.events.get(), 5)
                except TimeoutError:
                    if session.stopped.is_set():
                        return
                    yield ": heartbeat\n\n"
                    continue
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                if item["type"] in {"done", "error"}:
                    return
        finally:
            await manager.cancel(session_id)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{session_id}/finish")
async def finish_session(
    session_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    manager: Annotated[RealtimeDictation, Depends(realtime)],
) -> dict[str, str]:
    session = owned_session(manager, session_id, identity)
    try:
        return {"text": await manager.finish(session)}
    except DictationError as error:
        raise HTTPException(502, str(error)) from error
    finally:
        if not session.subscribed:
            await manager.cancel(session_id)


@router.delete("/sessions/{session_id}", status_code=204)
async def cancel_session(
    session_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    manager: Annotated[RealtimeDictation, Depends(realtime)],
) -> None:
    owned_session(manager, session_id, identity)
    await manager.cancel(session_id)
