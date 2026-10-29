"""Studio routes for user-owned projects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status

from harness.api.dependencies import (
    ApiContainer,
    Identity,
    ensure_permission,
    get_container,
    require_identity,
    require_readable_run,
)
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import ArtifactStatus
from harness.projects.models import (
    AttachProjectDocumentRequest,
    CreateProjectLinkRequest,
    CreateProjectMemoryRequest,
    CreateProjectRequest,
    ExtractProjectSkillRequest,
    FinalizeProjectResultRequest,
    Project,
    ProjectDocument,
    ProjectLink,
    ProjectMemory,
    ProjectResources,
    ProjectResult,
    ProjectSkill,
    SaveProjectResultRequest,
    SaveProjectSkillRequest,
    UpdateProjectMemoryRequest,
    UpdateProjectRequest,
    UpdateProjectSkillRequest,
)
from harness.projects.service import ProjectService

router = APIRouter(prefix="/v1/studio/projects", tags=["projects"])


def get_project_service(request: Request) -> ProjectService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "projects", None)
    if not isinstance(service, ProjectService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "project_control_plane_not_configured",
                "message": "Project store is not configured",
            },
        )
    return service


def _not_found(error: NotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "project_not_found", "message": str(error)},
    )


def _conflict(error: ConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "project_conflict", "message": str(error)},
    )


@router.get("", response_model=list[Project])
async def list_projects(
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
    include_archived: bool = False,
) -> list[Project]:
    ensure_permission(identity, "tasks:read")
    return await service.list(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        include_archived=include_archived,
    )


@router.post("", response_model=Project, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: CreateProjectRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> Project:
    ensure_permission(identity, "tasks:write")
    try:
        return await service.create(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            name=body.name,
            instructions=body.instructions or "",
            skill_references=body.skill_references or (),
            knowledge_references=body.knowledge_references or (),
        )
    except ConflictError as error:
        raise _conflict(error) from error


@router.patch("/{project_id}", response_model=Project)
async def update_project(
    project_id: str,
    body: UpdateProjectRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> Project:
    ensure_permission(identity, "tasks:write")
    if (
        body.name is None
        and body.instructions is None
        and body.skill_references is None
        and body.knowledge_references is None
        and body.mcp_references is None
        and body.pinned is None
        and body.archived is None
    ):
        raise ConflictError("No project update was requested")
    try:
        return await service.update(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
            name=body.name,
            instructions=body.instructions,
            skill_references=body.skill_references,
            knowledge_references=body.knowledge_references,
            mcp_references=body.mcp_references,
            pinned=body.pinned,
            archived=body.archived,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> None:
    ensure_permission(identity, "tasks:write")
    try:
        await service.delete(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
        )
    except NotFoundError as error:
        raise _not_found(error) from error


@router.get("/{project_id}/resources", response_model=ProjectResources)
async def project_resources(
    project_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectResources:
    ensure_permission(identity, "tasks:read")
    try:
        return await service.resources(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
        )
    except NotFoundError as error:
        raise _not_found(error) from error


@router.post(
    "/{project_id}/memories",
    response_model=ProjectMemory,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_memory(
    project_id: str,
    body: CreateProjectMemoryRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectMemory:
    ensure_permission(identity, "tasks:write")
    try:
        return await service.add_memory(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
            content=body.content,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.put("/{project_id}/memories/{memory_id}", response_model=ProjectMemory)
async def update_project_memory(
    project_id: str,
    memory_id: str,
    body: UpdateProjectMemoryRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectMemory:
    ensure_permission(identity, "tasks:write")
    try:
        return await service.update_memory(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
            memory_id=memory_id,
            content=body.content,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.delete("/{project_id}/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_memory(
    project_id: str,
    memory_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> None:
    ensure_permission(identity, "tasks:write")
    try:
        await service.delete_memory(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
            memory_id=memory_id,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.post(
    "/{project_id}/documents",
    response_model=ProjectDocument,
    status_code=status.HTTP_201_CREATED,
)
async def attach_project_document(
    project_id: str,
    body: AttachProjectDocumentRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectDocument:
    ensure_permission(identity, "tasks:write")
    try:
        return await service.attach_document(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
            input_artifact_id=body.input_artifact_id,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.delete(
    "/{project_id}/documents/{input_artifact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def detach_project_document(
    project_id: str,
    input_artifact_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> None:
    ensure_permission(identity, "tasks:write")
    try:
        await service.detach_document(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
            input_artifact_id=input_artifact_id,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.post(
    "/{project_id}/links",
    response_model=ProjectLink,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_link(
    project_id: str,
    body: CreateProjectLinkRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectLink:
    ensure_permission(identity, "tasks:write")
    try:
        return await service.add_link(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
            url=body.url,
            title=body.title,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.delete("/{project_id}/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_link(
    project_id: str,
    link_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> None:
    ensure_permission(identity, "tasks:write")
    try:
        await service.delete_link(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
            link_id=link_id,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.get("/{project_id}/results", response_model=list[ProjectResult])
async def list_project_results(
    project_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> list[ProjectResult]:
    ensure_permission(identity, "tasks:read")
    try:
        return await service.list_results(
            tenant_id=identity.tenant_id, user_id=identity.user_id, project_id=project_id
        )
    except NotFoundError as error:
        raise _not_found(error) from error


@router.post("/{project_id}/results", response_model=ProjectResult, status_code=201)
async def save_project_result(
    project_id: str,
    body: SaveProjectResultRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> ProjectResult:
    ensure_permission(identity, "tasks:write")
    try:
        existing = await service.list_results(
            tenant_id=identity.tenant_id, user_id=identity.user_id, project_id=project_id
        )
        for item in existing:
            if item.artifact_id == body.artifact_id:
                if body.add_to_resources:
                    await service.attach_document(
                        tenant_id=identity.tenant_id,
                        user_id=identity.user_id,
                        project_id=project_id,
                        input_artifact_id=item.input_artifact_id,
                    )
                return item
        artifact = await container.artifacts.get(identity.tenant_id, body.artifact_id)
        run, _session = await require_readable_run(container, identity, artifact.run_id)
        binding = await container.agui.get_thread_record_for_session(
            tenant_id=identity.tenant_id, user_id=identity.user_id, session_id=run.session_id
        )
        if binding.project_id != project_id or artifact.status is not ArtifactStatus.READY:
            raise NotFoundError("项目任务成果不存在")
        # The copied file survives later edits, archive or moves of the source task.
        _artifact, content = await container.artifacts.download(
            identity.tenant_id, artifact.artifact_id
        )
        snapshot = await container.input_artifacts.upload(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            name=artifact.name,
            media_type=artifact.media_type,
            content=content,
        )
        saved = await service.save_result(
            ProjectResult(
                resultId=f"project_result_{uuid4().hex}",
                tenantId=identity.tenant_id,
                userId=identity.user_id,
                projectId=project_id,
                artifactId=artifact.artifact_id,
                inputArtifactId=snapshot.input_artifact_id,
                threadId=binding.thread_id,
                threadTitle=binding.title or "未命名任务",
                runId=run.run_id,
                name=artifact.name,
                mediaType=artifact.media_type,
                sizeBytes=snapshot.size_bytes or 0,
                sha256=snapshot.sha256,
                createdAt=datetime.now(UTC),
            )
        )
        if body.add_to_resources:
            await service.attach_document(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                project_id=project_id,
                input_artifact_id=saved.input_artifact_id,
            )
        return saved
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.patch("/{project_id}/results/{result_id}", response_model=ProjectResult)
async def finalize_project_result(
    project_id: str,
    result_id: str,
    body: FinalizeProjectResultRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectResult:
    ensure_permission(identity, "tasks:write")
    try:
        return await service.finalize_result(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
            result_id=result_id,
            finalized=body.finalized,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.get("/{project_id}/web-search")
async def search_project_web(
    project_id: str,
    query: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> dict[str, object]:
    import ipaddress
    from urllib.parse import urlsplit

    from harness.runtime.web_tools import WebAccessError, is_public_address, validate_public_url

    def public_url(value: str) -> str:
        url = validate_public_url(value)
        host = urlsplit(url).hostname or ""
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not is_public_address(host):
                raise WebAccessError("仅支持公网 HTTP(S) 地址。")
        return url

    ensure_permission(identity, "tasks:write")
    try:
        await service.get(
            tenant_id=identity.tenant_id, user_id=identity.user_id, project_id=project_id
        )
        if query.strip().startswith(("https://", "http://")):
            url = public_url(query.strip())
            return {
                "results": [{"url": url, "title": urlsplit(url).hostname or url, "content": ""}]
            }
        client = await container.web_configurations.client_for_user(
            identity.tenant_id, identity.user_id
        )
        payload = await client.search(query, include_images=False, max_results=8)
        results = []
        seen = set()
        for item in payload.get("results", []):
            try:
                url = public_url(str(item.get("url", "")))
            except WebAccessError:
                continue
            if url in seen:
                continue
            seen.add(url)
            results.append(
                {
                    "url": url,
                    "title": str(item.get("title") or urlsplit(url).hostname)[:200],
                    "content": str(item.get("content") or "")[:1000],
                }
            )
        return {"results": results}
    except NotFoundError as error:
        raise _not_found(error) from error
    except WebAccessError as error:
        raise HTTPException(
            status_code=400, detail={"code": "project_web_search_failed", "message": str(error)}
        ) from error


@router.post("/{project_id}/skills", response_model=ProjectSkill, status_code=201)
async def save_project_skill(
    project_id: str,
    body: SaveProjectSkillRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> ProjectSkill:
    ensure_permission(identity, "tasks:write")
    try:
        if body.source_thread_id:
            binding = await container.agui.get_thread_record(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                thread_id=body.source_thread_id,
            )
            if binding.project_id != project_id:
                raise NotFoundError("来源任务不属于当前项目")
        return await service.save_skill(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
            display_name=body.display_name,
            skill=body.skill,
            source_thread_id=body.source_thread_id,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.patch("/{project_id}/skills/{skill_id}", response_model=ProjectSkill)
async def toggle_project_skill(
    project_id: str,
    skill_id: str,
    body: UpdateProjectSkillRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectSkill:
    ensure_permission(identity, "tasks:write")
    try:
        return await service.set_skill_enabled(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
            skill_id=skill_id,
            revision=body.revision,
            enabled=body.enabled,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.delete("/{project_id}/skills/{skill_id}", status_code=204)
async def delete_project_skill(
    project_id: str,
    skill_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> None:
    ensure_permission(identity, "tasks:write")
    try:
        await service.delete_skill(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
            skill_id=skill_id,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.post("/{project_id}/skills/extract")
async def extract_project_skill(
    project_id: str,
    body: ExtractProjectSkillRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
    container: Annotated[ApiContainer, Depends(get_container)],
):
    from harness.agui.routes import read_run_history
    from harness.studio.skill_builder import (
        SkillConversationContext,
        SkillConversationMessage,
        SkillConversationRequest,
        SkillConversationUnavailableError,
        SkillConversationUpstreamError,
    )

    ensure_permission(identity, "tasks:write")
    try:
        project = await service.get(
            tenant_id=identity.tenant_id, user_id=identity.user_id, project_id=project_id
        )
        binding = await container.agui.get_thread_record(
            tenant_id=identity.tenant_id, user_id=identity.user_id, thread_id=body.thread_id
        )
        if binding.project_id != project_id:
            raise NotFoundError("来源任务不属于当前项目")
        if container.skill_conversation is None:
            raise ConflictError("技能生成服务未配置")
        runs = await container.runs.list_for_sessions(
            identity.tenant_id, list(binding.session_ids), limit=4
        )
        source = []
        for run in sorted(runs, key=lambda run: run.created_at):
            for message in await read_run_history(container, identity, run):
                if message.role not in {"user", "assistant"}:
                    continue
                content = (
                    message.content
                    if isinstance(message.content, str)
                    else "\n".join(part.text for part in message.content if hasattr(part, "text"))
                )
                if content:
                    source.append(f"{message.role}: {content[:6000]}")
        if body.artifact_id:
            artifact = await container.artifacts.get(identity.tenant_id, body.artifact_id)
            _run, _session = await require_readable_run(container, identity, artifact.run_id)
            if _run.session_id not in binding.session_ids:
                raise NotFoundError("来源文件不属于当前任务")
            if (artifact.size_bytes or 0) <= 128 * 1024 and (
                artifact.media_type.startswith("text/")
                or artifact.name.endswith((".md", ".py", ".json", ".yaml"))
            ):
                _, content = await container.artifacts.download(
                    identity.tenant_id, artifact.artifact_id
                )
                source_text = content.decode("utf-8", errors="replace")[:16000]
                source.append(f"来源文件 {artifact.name}:\n{source_text}")
        if not source:
            raise ConflictError("任务还没有可提炼的对话或文件")
        route = body.model_route
        if not route:
            catalog = await container.capability_catalogs.get_for_user(
                identity.tenant_id, identity.user_id
            )
            route = next(
                (
                    item.route_id
                    for item in catalog.catalog.model_routes
                    if item.enabled and item.model_type in {"chat", "vision"}
                ),
                None,
            )
        if not route:
            raise ConflictError("请先配置可用的对话模型")
        transcript = "\n\n".join(source)[-24000:]
        goal = (
            "请将以下已完成任务中可复用的方法提炼成独立的项目 Skill 草稿。"
            "不执行原业务任务，不修改任何智能体。"
            "把企业、客户、时间等本次数据参数化，不包含凭据；仅保留有依据的步骤、输入输出契约和核验要求。"
            "可附带来源中已有的脚本或模板，不得声称运行过新生成的代码。"
            "信息足够时直接返回 ready 草稿。\n" + body.goal
        )
        messages = tuple(
            SkillConversationMessage(role="user", content=chunk)
            for chunk in [transcript[:12000], transcript[12000:]]
            if chunk
        )
        reply = await container.skill_conversation.respond(
            identity.tenant_id,
            SkillConversationRequest(
                modelRoute=route,
                context=SkillConversationContext(
                    agentName="project-skill",
                    displayName=project.name[:100],
                    domain="project",
                    description="项目工作流程提炼；与智能体配置及发布独立",
                ),
                messages=(*messages, SkillConversationMessage(role="user", content=goal)),
            ),
        )
        return reply
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error
    except SkillConversationUnavailableError as error:
        raise HTTPException(
            status_code=409, detail={"code": "skill_model_unavailable", "message": str(error)}
        ) from error
    except SkillConversationUpstreamError as error:
        raise HTTPException(
            status_code=502, detail={"code": "skill_generation_failed", "message": str(error)}
        ) from error
