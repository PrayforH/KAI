"""Project durable Harness facts into one replayable AG-UI ActivityMessage."""

import json
from collections.abc import Sequence
from typing import Any, cast

from ag_ui.core import ActivityDeltaEvent, ActivitySnapshotEvent, BaseEvent

from harness.core.events import RunEvent
from harness.runtime.audit_redaction import redact_text, redact_tool_arguments
from harness.runtime.input_redaction import redact_internal_agent_asset_events
from harness.runtime.message_mapper import safe_model_text

ACTIVITY_TYPE = "harness.run.v1"


def _timestamp(event: RunEvent) -> str:
    return event.timestamp.isoformat().replace("+00:00", "Z")


def _metadata(**values: object) -> dict[str, object]:
    return {key: value for key, value in values.items() if value is not None}


def _safe_tool_arguments(name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        return None
    return redact_tool_arguments(name, cast(dict[str, Any], arguments))


def _tool_result_summary(payload: dict[str, Any]) -> str | None:
    if payload.get("redacted") is True:
        if payload.get("redaction_reason") == "internal_agent_asset":
            return "内部 Skill / 提示词内容已隐藏"
        return "输入文件内容已隐藏"
    if payload.get("is_error") is True:
        error = payload.get("error")
        if isinstance(error, dict):
            error_values = cast(dict[str, Any], error)
            if error_values.get("code") == "policy_denied":
                return "权限 Profile 未放行此工具"
            message = error_values.get("message") or error_values.get("code")
            if isinstance(message, str) and message:
                return redact_text(message, limit=180)
        content = payload.get("content")
        if isinstance(content, str) and content.strip() == "no policy rule matched":
            return "权限 Profile 未放行此工具"
        return "工具返回错误"
    content = payload.get("content")
    if isinstance(content, str):
        stripped = content.strip()
        if not stripped:
            return "无输出"
        lines = len(stripped.splitlines())
        return (
            f"返回 {lines} 行 · {len(stripped)} 字符" if lines > 1 else f"返回 {len(stripped)} 字符"
        )
    if isinstance(content, list):
        values = cast(list[Any], content)
        return f"返回 {len(values)} 项"
    if isinstance(content, dict):
        values = cast(dict[str, Any], content)
        return f"返回 {len(values)} 个字段"
    return None


def _tool_result_preview(payload: dict[str, Any]) -> str | None:
    if payload.get("redacted") is True:
        return None
    content = payload.get("content")
    if isinstance(content, str):
        rendered = content
    elif isinstance(content, (list, dict)):
        # Claude SDK tool results commonly arrive as structured content blocks.
        # Keep a bounded, redacted preview instead of silently dropping the
        # result panel just because the provider did not flatten it to text.
        rendered = json.dumps(
            content,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    else:
        return None
    stripped = rendered.strip()
    if not stripped:
        return None
    return redact_text(stripped, limit=1_200)


_MAX_CITATIONS = 12
_MAX_CITATION_CONTENT = 4_000


def _tool_citations(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extract structured citations from a knowledge retrieval tool result.

    The console renders these as clickable chips so a reader can open the exact
    chunk that grounded an answer, instead of parsing the raw JSON preview. The
    shape check (``hits[].citation.chunkId``) identifies knowledge retrieval
    results without depending on which runtime emitted the tool result.
    """
    if payload.get("is_error") is True:
        return None
    if payload.get("redacted") is True:
        return None
    content = payload.get("content")
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in cast(list[Any], content):
            if isinstance(block, dict):
                block_values = cast(dict[str, Any], block)
                text = block_values.get("text")
                if isinstance(text, str):
                    texts.append(text)
    citations: list[dict[str, Any]] = []
    for text in texts:
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        hits = parsed.get("hits")
        if not isinstance(hits, list):
            continue
        for index, hit in enumerate(cast(list[Any], hits), start=1):
            if not isinstance(hit, dict):
                continue
            hit_values = cast(dict[str, Any], hit)
            citation = hit_values.get("citation")
            if not isinstance(citation, dict):
                continue
            citation_values = cast(dict[str, Any], citation)
            chunk_id = citation_values.get("chunkId")
            source_reference = citation_values.get("sourceReference")
            if not isinstance(chunk_id, str) or not isinstance(source_reference, str):
                continue
            citations.append(
                {
                    "index": index,
                    "chunkId": chunk_id,
                    "documentId": citation_values.get("documentId"),
                    "sourceReference": source_reference,
                    "knowledgeBaseReference": citation_values.get("knowledgeBaseReference"),
                    "sourceDisplayName": citation_values.get("sourceDisplayName"),
                    "snapshotId": citation_values.get("snapshotId"),
                    "title": citation_values.get("title"),
                    "uri": citation_values.get("uri"),
                    "score": hit_values.get("score"),
                    "content": redact_text(
                        str(hit_values.get("content") or ""),
                        limit=_MAX_CITATION_CONTENT,
                    ),
                }
            )
            if len(citations) >= _MAX_CITATIONS:
                return citations
    return citations or None


def _item(
    event: RunEvent,
    *,
    kind: str,
    status: str,
    title: str,
    summary: str | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, Any]:
    return {
        "id": event.event_id,
        "event_type": event.type,
        "kind": kind,
        "status": status,
        "title": title,
        "summary": summary,
        "timestamp": _timestamp(event),
        "sequence": event.sequence,
        "metadata": metadata or {},
    }


def _activity_item(event: RunEvent) -> dict[str, Any] | None:
    payload = event.payload
    if event.type == "run.queued":
        return _item(
            event,
            kind="run",
            status="queued",
            title=str(payload.get("reason") or "任务已加入队列"),
            summary=(str(payload["reason"]) if isinstance(payload.get("reason"), str) else None),
            metadata=_metadata(
                reason_code=payload.get("reason_code"),
                blocked_by_run_id=payload.get("blocked_by_run_id"),
                blocked_by_status=payload.get("blocked_by_status"),
            ),
        )
    if event.type == "workspace.restored":
        return _item(
            event,
            kind="analysis",
            status="succeeded",
            title="工作区已恢复",
            summary="已载入本会话上次保存的工作区",
        )
    if event.type == "context.recovery.loaded":
        return _item(
            event,
            kind="analysis",
            status="succeeded",
            title="上下文恢复点已载入",
            summary="已从脱敏摘要恢复事实、决定、待办与耐久对象引用",
        )
    if event.type == "agent.assets.staged":
        skills = payload.get("skills")
        skill_count = len(cast(list[object], skills)) if isinstance(skills, list) else 0
        return _item(
            event,
            kind="analysis",
            status="succeeded",
            title="Agent 资源已准备",
            summary=f"已装载 {skill_count} 个技能" if skill_count else None,
            metadata=_metadata(skill_count=skill_count or None),
        )
    if event.type == "policy.resolved":
        policy_id = payload.get("policy_id")
        return _item(
            event,
            kind="analysis",
            status="succeeded",
            title="运行权限已确认",
            summary=str(policy_id) if policy_id else None,
            metadata=_metadata(policy_id=policy_id),
        )
    if event.type == "credential.lease.issued":
        return _item(
            event,
            kind="analysis",
            status="succeeded",
            title="模型访问已就绪",
        )
    if event.type == "tool.directory.loaded":
        entry_count = payload.get("entry_count")
        return _item(
            event,
            kind="analysis",
            status="succeeded",
            title="工具能力已加载",
            summary=(f"已加载 {entry_count} 项工具能力" if isinstance(entry_count, int) else None),
            metadata=_metadata(entry_count=entry_count),
        )
    if event.type == "tool.directory.degraded":
        references = payload.get("references")
        safe_references = (
            [str(item) for item in cast(list[object], references) if isinstance(item, str)]
            if isinstance(references, list)
            else []
        )
        return _item(
            event,
            kind="analysis",
            status="succeeded",
            title="部分工具暂不可用",
            summary=(
                f"{'、'.join(safe_references)} 缺少运行凭据；其余能力继续执行"
                if safe_references
                else "部分 MCP 缺少运行凭据；其余能力继续执行"
            ),
            metadata=_metadata(
                references=safe_references or None,
                reason=payload.get("reason"),
                tool_count=payload.get("tool_count"),
            ),
        )
    if event.type == "workspace.archived":
        return _item(
            event,
            kind="analysis",
            status="succeeded",
            title="工作区状态已保存",
        )
    if event.type == "workspace.recovery_retained":
        retention_seconds = payload.get("retention_seconds")
        return _item(
            event,
            kind="analysis",
            status="waiting",
            title="工作区已保留用于恢复",
            summary=(
                f"持久化失败，远程工作区将临时保留 {retention_seconds // 60} 分钟"
                if isinstance(retention_seconds, int)
                else "持久化失败，远程工作区已临时保留"
            ),
            metadata=_metadata(retention_seconds=retention_seconds),
        )
    run_titles = {
        "run.provisioning": ("running", "正在准备运行环境"),
        "run.running": ("running", "Agent 开始执行"),
        "run.resumed": ("running", "已恢复执行"),
        "run.cancelling": ("waiting", "正在停止运行"),
        "run.cancelled": ("cancelled", "运行已停止"),
        "run.succeeded": ("succeeded", "运行完成"),
        "run.failed": ("failed", "运行失败"),
        "run.rejected": ("failed", "运行被拒绝"),
        "run.timed_out": ("failed", "运行超时"),
    }
    if event.type in run_titles:
        status, title = run_titles[event.type]
        error_code = payload.get("error_code")
        error_type = payload.get("error_type")
        subtype = payload.get("subtype")
        raw_message = str(payload["message"]) if isinstance(payload.get("message"), str) else None
        summary = raw_message
        if error_code == "provider_content_rejected":
            title = "模型服务拒绝了本轮上下文"
        elif error_type in {"CredentialLeaseError", "McpCredentialError"}:
            title = "运行凭据不可用"
            summary = (
                "当前 Agent 的模型或 MCP 凭据未配置、已过期或不在当前身份范围内。"
                "请在 Studio 检查能力连接后重试。"
            )
        elif subtype == "error_max_turns":
            title = "达到最大执行回合数"
            summary = "Agent 多次调用工具后仍未完成任务，请查看处理过程中的失败动作。"
        elif subtype == "error_max_budget_usd":
            title = "达到运行费用上限"
            summary = "此历史运行触发了费用额度上限；平台现已取消费用和 Token 执行限制。"
        elif (
            error_type == "ToolResolutionError"
            and raw_message is not None
            and "published MCP tools are no longer available" in raw_message
        ):
            title = "Agent 工具配置需要更新"
            summary = (
                "当前版本绑定的 MCP 工具已变化，请切换到最新版本，或在 Studio 中重新检查并发布。"
            )
        return _item(
            event,
            kind="error" if status == "failed" else "run",
            status=status,
            title=title,
            summary=summary,
            metadata=_metadata(
                error_code=error_code,
                error_type=error_type,
                subtype=subtype,
                diagnostic=(
                    redact_text(raw_message, limit=400)
                    if summary != raw_message and raw_message is not None
                    else None
                ),
            ),
        )
    if event.type == "model.route.selected":
        model = payload.get("model")
        return _item(
            event,
            kind="run",
            status="succeeded",
            title="模型路由已选择",
            summary=str(model) if model is not None else None,
            metadata=_metadata(
                provider=payload.get("provider"),
                model=model,
                used_fallback=payload.get("used_fallback"),
            ),
        )
    if event.type == "context.compaction.started":
        trigger = str(payload.get("trigger", "auto"))
        return _item(
            event,
            kind="analysis",
            status="running",
            title="正在压缩长上下文",
            summary=(
                "上下文接近模型窗口上限，正在保留关键事实并释放空间"
                if trigger == "auto"
                else "正在按请求整理并压缩历史上下文"
            ),
            metadata=_metadata(
                trigger=trigger,
                run_context_trust=payload.get("run_context_trust"),
                custom_instructions_supplied=payload.get("custom_instructions_supplied"),
            ),
        )
    if event.type == "runtime.system":
        subtype = str(payload.get("subtype", ""))
        if subtype == "thinking_tokens":
            return None
        status = str(payload.get("status", ""))
        title = (
            "运行时与工具已连接"
            if subtype == "init"
            else "正在压缩长上下文"
            if status == "compacting"
            else "模型正在处理"
            if status == "requesting"
            else "运行时状态更新"
        )
        tool_count = len(payload["tools"]) if isinstance(payload.get("tools"), list) else None
        return _item(
            event,
            kind="analysis",
            status="running",
            title=title,
            summary=(
                f"{tool_count} 项工具可用"
                if subtype == "init" and tool_count is not None
                else "正在生成可恢复的上下文摘要"
                if status == "compacting"
                else "正在等待本轮模型结果"
                if status == "requesting"
                else status or None
            ),
            metadata=_metadata(subtype=subtype or None, tool_count=tool_count),
        )
    if event.type == "message.delta":
        text = safe_model_text(str(payload.get("text", "")))
        if not text:
            return None
        return _item(
            event,
            kind="analysis",
            status="succeeded",
            title="进展说明",
            summary=redact_text(text, limit=max(1, len(text))),
            metadata=_metadata(message_id=payload.get("message_id")),
        )
    if event.type == "reasoning.summary.delta":
        text = safe_model_text(str(payload.get("text", "")))
        if not text:
            return None
        return _item(
            event,
            kind="analysis",
            status="succeeded",
            title="思考摘要",
            summary=redact_text(text, limit=max(1, len(text))),
            metadata=_metadata(item_id=payload.get("item_id")),
        )
    if event.type == "message.start":
        return _item(
            event,
            kind="analysis",
            status="running",
            title="正在生成本轮回复",
            metadata=_metadata(message_id=payload.get("message_id")),
        )
    if event.type == "message.completed":
        return _item(
            event,
            kind="analysis",
            status="succeeded",
            title="本轮回复已生成",
            metadata=_metadata(message_id=payload.get("message_id")),
        )
    if event.type == "tool.request":
        name = str(payload.get("name", "工具"))
        return _item(
            event,
            kind="subagent" if name in {"Task", "Agent"} else "tool",
            status="running",
            title=f"调用 {name}",
            metadata=_metadata(
                name=name,
                tool_call_id=payload.get("tool_call_id"),
                arguments=_safe_tool_arguments(name, payload),
            ),
        )
    if event.type in {"tool.result", "tool.allowed"}:
        failed = bool(payload.get("is_error"))
        return _item(
            event,
            kind="tool",
            status="failed" if failed else "succeeded",
            title="工具调用失败" if failed else "工具调用完成",
            metadata=_metadata(
                tool_call_id=payload.get("tool_call_id"),
                result_summary=_tool_result_summary(payload),
                result_preview=_tool_result_preview(payload),
                citations=_tool_citations(payload),
            ),
        )
    if event.type == "approval.requested":
        return _item(
            event,
            kind="tool",
            status="waiting",
            title="等待人工审批",
            summary=str(payload.get("reason")) if payload.get("reason") else None,
            metadata=_metadata(
                approval_id=payload.get("approval_id"),
                tool_call_id=payload.get("tool_call_id"),
            ),
        )
    if event.type in {"approval.approved", "approval.rejected"}:
        approved = event.type.endswith("approved")
        return _item(
            event,
            kind="tool",
            status="succeeded" if approved else "failed",
            title="审批已通过" if approved else "审批已拒绝",
            metadata=_metadata(approval_id=payload.get("approval_id")),
        )
    if event.type.startswith("subagent."):
        if event.type == "subagent.delta":
            return None
        failed = event.type.endswith("failed")
        completed = event.type.endswith("completed")
        return _item(
            event,
            kind="subagent",
            status="failed" if failed else "succeeded" if completed else "running",
            title=(
                "子 Agent 执行失败"
                if failed
                else "子 Agent 已完成"
                if completed
                else "子 Agent 正在执行"
            ),
            summary=str(payload.get("summary") or payload.get("description") or "") or None,
            metadata=_metadata(
                task_id=payload.get("task_id"),
                parent_tool_use_id=payload.get("parent_tool_use_id"),
                task_type=payload.get("task_type"),
                alias=payload.get("alias"),
                agent_name=payload.get("agent_name"),
                agent_version=payload.get("agent_version"),
                policy_profile=payload.get("policy_profile"),
                depth=payload.get("depth"),
                duration_ms=payload.get("duration_ms"),
                usage=payload.get("usage"),
                error_code=payload.get("error_code"),
                last_tool_name=payload.get("last_tool_name"),
            ),
        )
    if event.type == "artifact.ready":
        return _item(
            event,
            kind="artifact",
            status="succeeded",
            title="产物已就绪",
            summary=str(payload.get("name")) if payload.get("name") else None,
            metadata=_metadata(
                artifact_id=payload.get("artifact_id"),
                media_type=payload.get("media_type"),
                size_bytes=payload.get("size_bytes"),
                source_path=payload.get("source_path"),
                source=payload.get("source"),
            ),
        )
    if event.type == "runtime.result":
        failed = bool(payload.get("is_error"))
        subtype = payload.get("subtype")
        turns = payload.get("num_turns")
        summary = None
        if failed and subtype == "error_max_turns":
            summary = (
                f"已用完 {turns} 个模型回合，任务尚未完成"
                if isinstance(turns, int)
                else "已达到最大模型回合数，任务尚未完成"
            )
        if failed and subtype == "error_max_budget_usd":
            summary = "此历史运行触发了费用额度上限；平台现已取消费用和 Token 执行限制。"
        return _item(
            event,
            kind="result",
            status="failed" if failed else "succeeded",
            title=(
                "达到最大模型回合数"
                if failed and subtype == "error_max_turns"
                else "达到运行费用上限"
                if failed and subtype == "error_max_budget_usd"
                else "模型执行失败"
                if failed
                else "模型执行完成"
            ),
            summary=summary,
            metadata=_metadata(
                subtype=subtype,
                turns=turns,
                cost_usd=payload.get("total_cost_usd"),
                usage=payload.get("usage"),
                stop_reason=payload.get("stop_reason"),
            ),
        )
    return None


def build_run_activity(events: Sequence[RunEvent]) -> dict[str, Any] | None:
    """Fold durable run events into the same final activity used by live AG-UI."""
    if not events:
        return None
    events = redact_internal_agent_asset_events(events)
    items: list[dict[str, Any]] = []
    metrics: dict[str, object] = {}
    status = "queued"
    for event in events:
        item = _activity_item(event)
        if item is None:
            continue
        items.append(item)
        if event.type.startswith("run.") or event.type == "runtime.result":
            status = str(item["status"])
        if event.type == "runtime.result":
            metrics.update(item["metadata"])
    if not items:
        return None
    first = next((event for event in events if event.type == "run.queued"), events[0])
    return {
        "run_id": first.run_id,
        "trace_id": first.trace_id,
        "status": status,
        "started_at": _timestamp(first),
        "items": items,
        "metrics": metrics,
    }


def activity_projection(event: RunEvent) -> list[BaseEvent]:
    item = _activity_item(event)
    if item is None:
        return []
    message_id = f"activity-{event.run_id}"
    if event.type == "run.queued":
        return [
            ActivitySnapshotEvent(
                message_id=message_id,
                activity_type=ACTIVITY_TYPE,
                content={
                    "run_id": event.run_id,
                    "trace_id": event.trace_id,
                    "status": "queued",
                    "started_at": _timestamp(event),
                    "items": [item],
                    "metrics": {},
                },
            )
        ]

    patch: list[dict[str, Any]] = [{"op": "add", "path": "/items/-", "value": item}]
    if event.type.startswith("run."):
        patch.append({"op": "replace", "path": "/status", "value": item["status"]})
    if event.type == "runtime.result":
        patch.append({"op": "replace", "path": "/status", "value": item["status"]})
        for key, value in item["metadata"].items():
            patch.append({"op": "add", "path": f"/metrics/{key}", "value": value})
    return [
        ActivityDeltaEvent(
            message_id=message_id,
            activity_type=ACTIVITY_TYPE,
            patch=patch,
        )
    ]
