"""Explicit, owner-scoped historical file delivery through configured channels."""

import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from harness.agui.service import AguiRunService
from harness.application.artifacts import ArtifactService
from harness.application.runs import RunService
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.models import ArtifactStatus, Run, RunStatus, Session
from harness.feishu.service import FeishuService
from harness.runtime.platform_tools import PlatformTool, PlatformToolOverlay


class FileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["list", "send", "status"]
    artifact_id: str | None = Field(default=None, min_length=1, max_length=128)
    delivery_id: str | None = Field(default=None, min_length=1, max_length=128)
    request_key: str | None = Field(default=None, min_length=1, max_length=80)


async def assistant_file_overlay(
    artifacts: ArtifactService, runs: RunService, agui: AguiRunService,
    service: Callable[[], Awaitable[FeishuService]], run: Run, session: Session,
) -> PlatformToolOverlay:
    origin = await agui.get_thread_record_for_session(
        tenant_id=run.tenant_id, user_id=session.user_id, session_id=run.session_id,
    )

    async def check_scope(artifact_id: str) -> None:
        artifact = await artifacts.get(run.tenant_id, artifact_id)
        original = await runs.get(run.tenant_id, artifact.run_id)
        thread = await agui.get_thread_record_for_session(
            tenant_id=run.tenant_id, user_id=session.user_id, session_id=original.session_id,
        )
        if thread.thread_id != origin.thread_id:
            raise PermissionDeniedError("文件不属于当前会话")

    async def handle(arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            request = FileRequest.model_validate(arguments)
            current = await runs.get(run.tenant_id, run.run_id)
            if (current.status is not RunStatus.RUNNING
                    or current.fencing_token != run.fencing_token):
                raise ConflictError("本次执行已结束")
            feishu = await service()
            await feishu.authorize(run.tenant_id, session.user_id)
            if request.action == "list":
                history = await runs.list_for_sessions(
                    run.tenant_id, list(origin.session_ids), limit=200,
                )
                order = {entry.run_id: entry.created_at for entry in history}
                files = await artifacts.list_for_runs(run.tenant_id, list(order))
                files.sort(key=lambda item: (order[item.run_id], item.artifact_id), reverse=True)
                connection = await feishu.status(run.tenant_id, session.user_id)
                result: object = {
                    "destination": {"channel": "feishu", "bound": connection.bound,
                                    "enabled": connection.enabled},
                    "files": [{"artifactId": item.artifact_id, "name": item.name,
                               "mediaType": item.media_type, "sizeBytes": item.size_bytes}
                              for item in files if item.status is ArtifactStatus.READY][:20],
                }
            elif request.action == "send":
                if not request.artifact_id or not request.request_key:
                    raise ValueError("send需要artifact_id和request_key")
                result = await feishu.queue_artifact(
                    run.tenant_id, session.user_id, request.artifact_id, request.request_key,
                    thread_id=origin.thread_id,
                )
            else:
                if not request.delivery_id:
                    raise ValueError("status需要delivery_id")
                result = await feishu.file_receipt(
                    run.tenant_id, session.user_id, request.delivery_id,
                )
                await check_scope(str(result["artifactId"]))
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
        except (ValueError, ConflictError, NotFoundError, PermissionDeniedError) as error:
            return {"isError": True, "content": [{"type": "text", "text": str(error)}]}

    return PlatformToolOverlay(
        tools=(PlatformTool(
            name="assistant_file_delivery",
            description="列出本会话成果文件、发送用户指定的历史成果到本人已绑定飞书，或查询投递回执。",
            schema=FileRequest.model_json_schema(), handler=handle,
        ),),
        instructions=(
            "KAI成果发布与飞书文件投递是两种回执。assistant_file_delivery的list返回本会话"
            "历史成果和当前飞书绑定；用户要求发送文件时，以真实artifact_id调用send，重试沿用"
            "同一个request_key，用status核对sent后才说明已送达。应用凭证由平台管理，无需用户"
            "再次提供密钥或打开飞书客户端。仅发送用户要求的文件。"
        ),
    )
