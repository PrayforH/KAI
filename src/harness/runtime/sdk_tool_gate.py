"""Claude SDK PreToolUse bridge to Harness policy and approvals."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast

from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import (
    HookContext,
    HookEvent,
    HookInput,
    HookJSONOutput,
    PostToolUseFailureHookInput,
    PostToolUseHookInput,
    PreCompactHookInput,
    PreToolUseHookInput,
    SyncHookJSONOutput,
)

from harness.application.approvals import ApprovalService
from harness.application.events import EventService
from harness.context.service import ContextService
from harness.core.models import ApprovalStatus
from harness.observability.provider import Observability
from harness.policy.bash_safety import sandboxed_bash_is_low_risk
from harness.policy.models import (
    ContextTrust,
    PolicyContext,
    PolicyDecision,
    PolicyResult,
)
from harness.policy.profiles import PolicyProfileRegistry
from harness.policy.results import stricter_trust
from harness.policy.rules import PolicyEngine
from harness.quota.models import QuotaResource
from harness.quota.repositories import QuotaExceededError
from harness.quota.service import QuotaService
from harness.runtime.audit_redaction import redact_text, redact_tool_arguments
from harness.runtime.base import RuntimeContext
from harness.runtime.input_redaction import (
    INTERNAL_AGENT_ASSET_MARKER,
    STAGED_INPUT_READ_MARKER,
    internal_agent_asset_access,
    staged_input_paths,
    staged_read_path,
)
from harness.runtime.sandbox_tools import canonical_tool_name, proxy_tool_name


class ToolGate(Protocol):
    def hooks(
        self,
        context: RuntimeContext,
        *,
        policy_id: str | None = None,
        subagent_policy_ids: Mapping[str, str] | None = None,
        result_trust_by_tool: Mapping[str, ContextTrust] | None = None,
        delegate_allowed_to_sdk_permissions: bool = False,
    ) -> dict[HookEvent, list[HookMatcher]]: ...


_APPROVAL_ARGUMENT_KEYS = (
    "command",
    "file_path",
    "path",
    "query",
    "url",
    "urls",
    "description",
    "subagent_type",
    "pattern",
    "glob",
)

_TRUST_PRECEDENCE = {
    ContextTrust.SAFE: 0,
    ContextTrust.SENSITIVE: 1,
    ContextTrust.UNTRUSTED: 2,
}


def _approval_argument_summary(arguments: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in _APPROVAL_ARGUMENT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            summary[key] = redact_text(value)
        elif isinstance(value, list):
            values = cast(list[object], value)
            if all(isinstance(item, str) for item in values):
                summary[key] = [
                    redact_text(item, limit=200) for item in values[:5] if isinstance(item, str)
                ]
    return summary


def _approval_risk(tool_name: str) -> str:
    if tool_name == "Bash":
        return "high"
    if tool_name in {"Write", "Edit"}:
        return "medium"
    return "low"


def _hook_output(
    decision: str | None,
    reason: str,
    *,
    updated_input: dict[str, Any] | None = None,
) -> SyncHookJSONOutput:
    specific: dict[str, Any] = {"hookEventName": "PreToolUse"}
    if decision is not None:
        specific["permissionDecision"] = decision
        specific["permissionDecisionReason"] = reason
    if updated_input is not None:
        specific["updatedInput"] = updated_input
    return cast(
        SyncHookJSONOutput,
        {
            "hookSpecificOutput": specific,
        },
    )


class _RunFileCapabilities:
    """Track successful, run-created files without trusting model claims."""

    def __init__(self, context: RuntimeContext) -> None:
        self._workspace = context.workspace.resolve()
        self._remote_workspace = (
            PurePosixPath(context.remote_workspace)
            if context.remote_workspace is not None
            else None
        )
        self._initial_exists: dict[Path, bool] = {}
        self._generated: set[Path] = set()
        self._pending_writes: dict[str, Path] = {}
        self._protected: set[Path] = set()
        for value in (*context.input_files, *context.processed_input_paths):
            target = self._normalize(value)
            if target is not None:
                self._protected.add(target)

    def _normalize(self, value: str) -> Path | None:
        if not value.strip():
            return None
        pure = PurePosixPath(value)
        if pure.is_absolute() and len(pure.parts) >= 2 and pure.parts[1] == "workspace":
            candidate = self._workspace.joinpath(*pure.parts[2:])
        elif (
            pure.is_absolute()
            and self._remote_workspace is not None
            and pure.is_relative_to(self._remote_workspace)
        ):
            candidate = self._workspace.joinpath(*pure.relative_to(self._remote_workspace).parts)
        else:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = self._workspace / candidate
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            return None
        if resolved == self._workspace or not resolved.is_relative_to(self._workspace):
            return None
        return resolved

    def target(self, arguments: dict[str, Any]) -> Path | None:
        value = arguments.get("file_path", arguments.get("path"))
        return self._normalize(value) if isinstance(value, str) else None

    def normalize_skill_read(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Map stale HOME/temp Skill references to one immutable workspace file."""

        value = arguments.get("file_path")
        if not isinstance(value, str) or not value.strip():
            return None
        pure = PurePosixPath(value)
        parts = pure.parts
        current = self._normalize(value)
        if current is not None and current.is_file():
            if pure.is_absolute() and len(parts) >= 2 and parts[1] == "workspace":
                updated = dict(arguments)
                updated["file_path"] = str(current)
                return updated
            return None

        if pure.is_absolute() and "inputs" in parts:
            input_index = parts.index("inputs")
            input_candidate = self._workspace.joinpath(*parts[input_index:])
            try:
                resolved_input = input_candidate.resolve(strict=True)
            except (FileNotFoundError, OSError, RuntimeError):
                resolved_input = None
            if (
                resolved_input is not None
                and resolved_input.is_relative_to(self._workspace)
                and resolved_input.is_file()
            ):
                updated = dict(arguments)
                updated["file_path"] = str(resolved_input)
                return updated

        skills_root = self._workspace / ".claude" / "skills"
        relative: PurePosixPath | None = None
        for index in range(len(parts) - 2):
            if parts[index : index + 2] == (".claude", "skills"):
                relative = PurePosixPath(*parts[index:])
                break

        candidates: list[Path] = []
        if relative is not None:
            candidates.append(self._workspace.joinpath(*relative.parts))
        else:
            try:
                reference_index = parts.index("references")
            except ValueError:
                reference_index = -1
            reference_parts = parts[reference_index + 1 :] if reference_index >= 0 else ()
            if reference_parts and ".." not in reference_parts:
                for reference_root in skills_root.glob("*/references"):
                    if reference_root.is_dir():
                        candidates.append(reference_root.joinpath(*reference_parts))

        matches: list[Path] = []
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=False)
            except (OSError, RuntimeError):
                continue
            if (
                resolved.is_relative_to(skills_root)
                and resolved.is_file()
                and resolved not in matches
            ):
                matches.append(resolved)
        if len(matches) != 1:
            return None
        updated = dict(arguments)
        updated["file_path"] = matches[0].relative_to(self._workspace).as_posix()
        return updated

    def is_generated(self, target: Path) -> bool:
        return target in self._generated

    def generated_python_files(self) -> frozenset[str]:
        values: set[str] = set()
        for target in self._generated:
            if target.suffix.lower() != ".py":
                continue
            relative = target.relative_to(self._workspace).as_posix()
            values.update(
                {
                    relative,
                    f"./{relative}",
                    target.as_posix(),
                    f"/workspace/{relative}",
                }
            )
            if self._remote_workspace is not None:
                values.add((self._remote_workspace / relative).as_posix())
        return frozenset(values)

    def observe(self, target: Path) -> None:
        self._initial_exists.setdefault(target, target.exists())

    def note_authorized_write(self, tool_call_id: str, target: Path) -> None:
        existed = self._initial_exists[target]
        relative = target.relative_to(self._workspace)
        protected = target in self._protected or (
            bool(relative.parts) and relative.parts[0] == "inputs"
        )
        if not existed and not protected:
            self._pending_writes[tool_call_id] = target

    def note_success(self, hook_input: PostToolUseHookInput) -> None:
        target = self._pending_writes.pop(hook_input["tool_use_id"], None)
        if canonical_tool_name(hook_input["tool_name"]) == "Write" and target is not None:
            self._generated.add(target)

    def note_failure(self, hook_input: PostToolUseFailureHookInput) -> None:
        self._pending_writes.pop(hook_input["tool_use_id"], None)


class SdkToolGate:
    """Build a catch-all SDK hook that decides before tool execution."""

    def __init__(
        self,
        *,
        policy: PolicyEngine | None = None,
        profiles: PolicyProfileRegistry | None = None,
        approvals: ApprovalService,
        events: EventService,
        context_service: ContextService | None = None,
        quotas: QuotaService | None = None,
        observability: Observability | None = None,
    ) -> None:
        if (policy is None) == (profiles is None):
            raise ValueError("configure exactly one policy engine or profile registry")
        self._policy = policy
        self._profiles = profiles
        self._approvals = approvals
        self._events = events
        self._context_service = context_service
        self._quotas = quotas
        self._observability = observability

    def hooks(
        self,
        context: RuntimeContext,
        *,
        policy_id: str | None = None,
        subagent_policy_ids: Mapping[str, str] | None = None,
        result_trust_by_tool: Mapping[str, ContextTrust] | None = None,
        delegate_allowed_to_sdk_permissions: bool = False,
    ) -> dict[HookEvent, list[HookMatcher]]:
        active_policy_id = (
            context.resolved_policy.policy_id
            if context.resolved_policy is not None
            else policy_id or "local-standard"
        )
        policy = (
            context.resolved_policy.call_policy
            if context.resolved_policy is not None
            else self._profiles.resolve(active_policy_id)
            if self._profiles is not None
            else self._policy
        )
        assert policy is not None
        subagent_policies: dict[str, tuple[str, PolicyEngine]] = {}
        if subagent_policy_ids:
            if self._profiles is None:
                subagent_policies = {
                    name: (active_policy_id, policy) for name in subagent_policy_ids
                }
            else:
                subagent_policies = {
                    name: (child_policy_id, self._profiles.resolve(child_policy_id))
                    for name, child_policy_id in subagent_policy_ids.items()
                }
        implicit_deny = PolicyEngine([])
        file_capabilities = _RunFileCapabilities(context)
        tool_traces: dict[str, tuple[int, str, dict[str, Any], str]] = {}
        current_context_trust: ContextTrust | None = None
        pending_result_trust: dict[str, tuple[str, ContextTrust, str]] = {}
        declared_result_trust = dict(result_trust_by_tool or {})

        async def load_context_trust() -> ContextTrust:
            nonlocal current_context_trust
            if current_context_trust is not None:
                return current_context_trust
            if self._context_service is None:
                current_context_trust = ContextTrust.SAFE
            else:
                state = await self._context_service.state(
                    context.run.tenant_id,
                    context.session.user_id,
                    context.run.session_id,
                )
                current_context_trust = state.trust_high_watermark
            return current_context_trust

        def finish_tool_trace(
            hook_input: PostToolUseHookInput | PostToolUseFailureHookInput,
            *,
            status: str,
            error_type: str | None = None,
        ) -> None:
            if self._observability is None:
                return
            state = tool_traces.pop(hook_input["tool_use_id"], None)
            if state is None:
                return
            started_at_ns, tool_name, arguments, selected_policy_id = state
            ended_at_ns = time.time_ns()
            output_value: object = (
                {"error": str(hook_input.get("error", error_type or "tool_failed"))}
                if status != "succeeded"
                else {"status": status}
                if tool_name == "Read"
                else hook_input.get("tool_response", {"status": status})
            )
            self._observability.record_completed_span(
                tool_name,
                started_at_ns=started_at_ns,
                ended_at_ns=ended_at_ns,
                attributes={
                    "run.id": context.run.run_id,
                    "harness.tool.name": tool_name,
                    "harness.tool.call_id": hook_input["tool_use_id"],
                    "harness.tool.status": status,
                    "harness.tool.duration_ms": max(
                        0, round((ended_at_ns - started_at_ns) / 1_000_000)
                    ),
                    "harness.policy.profile": selected_policy_id,
                    "harness.sandbox.provider": context.sandbox_provider,
                    "harness.sandbox.isolation": context.sandbox_isolation.value,
                    "langfuse.observation.type": "tool",
                    "langfuse.observation.level": ("ERROR" if status != "succeeded" else "DEFAULT"),
                    "langfuse.observation.status_message": status,
                    "langfuse.observation.metadata.call_id": hook_input["tool_use_id"],
                    "langfuse.observation.metadata.policy": selected_policy_id,
                    "langfuse.observation.metadata.sandbox": (
                        f"{context.sandbox_provider}:{context.sandbox_isolation.value}"
                    ),
                },
                input_value=arguments,
                output_value=output_value,
                error_type=error_type,
            )

        async def pre_tool_use(
            hook_input: HookInput,
            _tool_use_id: str | None,
            _hook_context: HookContext,
        ) -> HookJSONOutput:
            typed_input = cast(PreToolUseHookInput, hook_input)
            selected_policy_id = active_policy_id
            selected_policy = policy
            if subagent_policies:
                agent_type = str(typed_input.get("agent_type") or "")
                agent_id = str(typed_input.get("agent_id") or "")
                key = (
                    agent_type
                    if agent_type in subagent_policies
                    else agent_id
                    if agent_id in subagent_policies
                    else ""
                )
                if key:
                    selected_policy_id, selected_policy = subagent_policies[key]
                elif agent_type or agent_id:
                    selected_policy_id = "unknown-subagent"
                    selected_policy = implicit_deny
            canonical_name = canonical_tool_name(typed_input["tool_name"])
            catalog_result_trust = declared_result_trust.get(
                typed_input["tool_name"],
                declared_result_trust.get(canonical_name, ContextTrust.SAFE),
            )
            result_policy_rule = "tool-catalog"
            result_trust = catalog_result_trust
            if context.resolved_policy is not None:
                result_policy = context.resolved_policy.result_policy.evaluate(
                    canonical_name,
                    agent_name=str(
                        typed_input.get("agent_type")
                        or typed_input.get("agent_id")
                        or context.session.agent_name
                    ),
                )
                result_trust = stricter_trust(
                    catalog_result_trust,
                    result_policy.trust,
                )
                if result_trust is result_policy.trust:
                    result_policy_rule = result_policy.rule_name
            if result_trust is not ContextTrust.SAFE:
                pending_result_trust[typed_input["tool_use_id"]] = (
                    canonical_name,
                    result_trust,
                    result_policy_rule,
                )
            tool_traces[typed_input["tool_use_id"]] = (
                time.time_ns(),
                canonical_name,
                redact_tool_arguments(
                    canonical_name,
                    dict(typed_input["tool_input"]),
                ),
                selected_policy_id,
            )
            context_trust = await load_context_trust()
            output = await self._authorize(
                context,
                typed_input,
                policy=selected_policy,
                policy_id=selected_policy_id,
                file_capabilities=file_capabilities,
                allowed_subagent_aliases=frozenset(subagent_policies),
                declared_tools=frozenset(declared_result_trust),
                context_trust=context_trust,
                delegate_allowed_to_sdk_permissions=delegate_allowed_to_sdk_permissions,
            )
            specific = cast(dict[str, Any], output).get("hookSpecificOutput", {})
            decision = (
                str(cast(dict[str, Any], specific).get("permissionDecision", ""))
                if isinstance(specific, dict)
                else ""
            )
            if decision == "deny":
                pending_result_trust.pop(typed_input["tool_use_id"], None)
                denied = cast(
                    PostToolUseFailureHookInput,
                    {
                        **typed_input,
                        "hook_event_name": "PostToolUseFailure",
                        "error": "policy denied",
                    },
                )
                finish_tool_trace(
                    denied,
                    status="denied",
                    error_type="policy_denied",
                )
            return output

        async def pre_compact(
            hook_input: HookInput,
            _tool_use_id: str | None,
            _hook_context: HookContext,
        ) -> HookJSONOutput:
            """Record a content-free boundary before the SDK rewrites its transcript."""

            typed_input = cast(PreCompactHookInput, hook_input)
            custom_instructions = typed_input.get("custom_instructions")
            context_trust = await load_context_trust()
            await self._events.append(
                tenant_id=context.run.tenant_id,
                run_id=context.run.run_id,
                session_id=context.run.session_id,
                event_type="context.compaction.started",
                payload={
                    "trigger": typed_input["trigger"],
                    "custom_instructions_supplied": bool(
                        isinstance(custom_instructions, str) and custom_instructions.strip()
                    ),
                    "session_context_trust": context_trust.value,
                },
            )
            return cast(HookJSONOutput, {})

        async def post_tool_use(
            hook_input: HookInput,
            _tool_use_id: str | None,
            _hook_context: HookContext,
        ) -> HookJSONOutput:
            file_capabilities.note_success(cast(PostToolUseHookInput, hook_input))
            return cast(HookJSONOutput, {})

        async def post_tool_use_failure(
            hook_input: HookInput,
            _tool_use_id: str | None,
            _hook_context: HookContext,
        ) -> HookJSONOutput:
            file_capabilities.note_failure(cast(PostToolUseFailureHookInput, hook_input))
            return cast(HookJSONOutput, {})

        async def trace_tool_success(
            hook_input: HookInput,
            _tool_use_id: str | None,
            _hook_context: HookContext,
        ) -> HookJSONOutput:
            nonlocal current_context_trust
            typed_input = cast(PostToolUseHookInput, hook_input)
            pending = pending_result_trust.pop(typed_input["tool_use_id"], None)
            loaded_trust = await load_context_trust()
            if pending is not None and (
                _TRUST_PRECEDENCE[pending[1]] > _TRUST_PRECEDENCE[loaded_trust]
            ):
                tool_name, next_trust, result_policy_rule = pending
                previous_trust = loaded_trust
                if self._context_service is None:
                    current_context_trust = next_trust
                else:
                    state = await self._context_service.promote_trust(
                        context.run.tenant_id,
                        context.session.user_id,
                        context.run.session_id,
                        next_trust,
                    )
                    current_context_trust = state.trust_high_watermark
                trust_payload = {
                    "tool_call_id": typed_input["tool_use_id"],
                    "tool_name": tool_name,
                    "previous": previous_trust.value,
                    "current": current_context_trust.value,
                }
                if result_policy_rule != "tool-catalog":
                    trust_payload["policy_rule"] = result_policy_rule
                await self._events.append(
                    tenant_id=context.run.tenant_id,
                    run_id=context.run.run_id,
                    session_id=context.run.session_id,
                    event_type="context.trust.changed",
                    payload=trust_payload,
                )
            finish_tool_trace(
                typed_input,
                status="succeeded",
            )
            return cast(HookJSONOutput, {})

        async def trace_tool_failure(
            hook_input: HookInput,
            _tool_use_id: str | None,
            _hook_context: HookContext,
        ) -> HookJSONOutput:
            typed_input = cast(PostToolUseFailureHookInput, hook_input)
            pending_result_trust.pop(typed_input["tool_use_id"], None)
            finish_tool_trace(
                typed_input,
                status="failed",
                error_type=redact_text(str(typed_input.get("error", "tool_failed"))),
            )
            return cast(HookJSONOutput, {})

        async def release_delegation(
            hook_input: HookInput,
            _tool_use_id: str | None,
            _hook_context: HookContext,
        ) -> HookJSONOutput:
            typed_input = cast(PostToolUseHookInput | PostToolUseFailureHookInput, hook_input)
            if self._quotas is not None:
                await self._quotas.release_idempotency(
                    context.run.tenant_id,
                    f"run:{context.run.run_id}:subagent:{typed_input['tool_use_id']}",
                )
            return cast(HookJSONOutput, {})

        return {
            "PreCompact": [HookMatcher(matcher=None, hooks=[pre_compact], timeout=30.0)],
            "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use], timeout=900.0)],
            "PostToolUse": [
                HookMatcher(
                    matcher=f"Write|{proxy_tool_name('Write')}",
                    hooks=[post_tool_use],
                ),
                HookMatcher(matcher="Task|Agent", hooks=[release_delegation]),
                HookMatcher(matcher=None, hooks=[trace_tool_success]),
            ],
            "PostToolUseFailure": [
                HookMatcher(
                    matcher=f"Write|{proxy_tool_name('Write')}",
                    hooks=[post_tool_use_failure],
                ),
                HookMatcher(matcher="Task|Agent", hooks=[release_delegation]),
                HookMatcher(matcher=None, hooks=[trace_tool_failure]),
            ],
        }

    async def _authorize(
        self,
        context: RuntimeContext,
        hook_input: PreToolUseHookInput,
        *,
        policy: PolicyEngine,
        policy_id: str,
        file_capabilities: _RunFileCapabilities,
        allowed_subagent_aliases: frozenset[str] = frozenset(),
        declared_tools: frozenset[str] = frozenset(),
        context_trust: ContextTrust = ContextTrust.SAFE,
        delegate_allowed_to_sdk_permissions: bool = False,
    ) -> SyncHookJSONOutput:
        raw_tool_name = hook_input["tool_name"]
        tool_name = canonical_tool_name(raw_tool_name)
        tool_call_id = hook_input["tool_use_id"]
        arguments = hook_input["tool_input"]
        updated_arguments = (
            file_capabilities.normalize_skill_read(arguments) if tool_name == "Read" else None
        )
        if tool_name == "Read" and arguments.get("pages") == "":
            updated_arguments = dict(updated_arguments or arguments)
            updated_arguments.pop("pages", None)
        if updated_arguments is not None:
            arguments = updated_arguments
        request_payload: dict[str, Any] = {
            "name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
            "policy_checked": True,
            "policy_profile": policy_id,
            "context_trust": context_trust.value,
            "message_id": context.assistant_message_id,
            "sandbox": {
                "provider": context.sandbox_provider,
                "isolation": context.sandbox_isolation.value,
            },
        }
        relative_input_path = staged_read_path(
            request_payload,
            workspace=context.workspace,
            staged_paths=staged_input_paths(context.workspace, context.input_files),
        )
        if relative_input_path is not None:
            safe_arguments = dict(arguments)
            safe_arguments["file_path"] = relative_input_path
            request_payload["arguments"] = safe_arguments
            request_payload[STAGED_INPUT_READ_MARKER] = True
        if internal_agent_asset_access(request_payload):
            request_payload[INTERNAL_AGENT_ASSET_MARKER] = True
        audit_arguments = request_payload.get("arguments")
        if isinstance(audit_arguments, dict):
            request_payload["arguments"] = redact_tool_arguments(
                tool_name, cast(dict[str, Any], audit_arguments)
            )
        agent_id = hook_input.get("agent_id")
        if agent_id:
            request_payload["agent_id"] = agent_id
        await self._events.append(
            tenant_id=context.run.tenant_id,
            run_id=context.run.run_id,
            session_id=context.run.session_id,
            event_type="tool.request",
            payload=request_payload,
        )
        if tool_name in {"Task", "Agent"} and allowed_subagent_aliases:
            requested_alias = next(
                (
                    str(arguments[key])
                    for key in ("subagent_type", "agent", "name")
                    if isinstance(arguments.get(key), str) and arguments[key]
                ),
                "",
            )
            if requested_alias not in allowed_subagent_aliases:
                reason = (
                    "subagent role is not declared by the published Agent Manifest; "
                    "available subagent_type values: "
                    + ", ".join(sorted(allowed_subagent_aliases))
                    + ". Use a declared role only when its tools support the task."
                )
                await self._append_denied(context, tool_call_id, reason)
                return _hook_output("deny", reason)
        write_target: Path | None = None
        if tool_name in {"Write", "Edit"}:
            write_target = file_capabilities.target(arguments)
            if write_target is None:
                reason = "write path must stay within the run workspace"
                await self._append_denied(context, tool_call_id, reason)
                return _hook_output("deny", reason)
            file_capabilities.observe(write_target)
        result = (
            PolicyResult(
                decision=PolicyDecision.ALLOW,
                rule_name="run-generated-file",
                reason="matched run-created file capability",
            )
            if write_target is not None and file_capabilities.is_generated(write_target)
            else policy.evaluate(
                PolicyContext(
                    tenant_id=context.run.tenant_id,
                    agent_name=context.session.agent_name,
                    tool_name=tool_name,
                    arguments=arguments,
                    sandbox_isolation=context.sandbox_isolation,
                    context_trust=context_trust,
                )
            )
        )
        if (
            tool_name == "Bash"
            and result.decision is PolicyDecision.ASK
            and sandboxed_bash_is_low_risk(
                str(arguments.get("command", "")),
                workspace=str(context.workspace),
                remote_workspace=context.remote_workspace,
                generated_python_files=file_capabilities.generated_python_files(),
            )
        ):
            result = PolicyResult(
                decision=PolicyDecision.ALLOW,
                rule_name="sandbox-low-risk-bash",
                reason="matched sandbox low-risk Bash policy",
            )
        if (
            result.decision is PolicyDecision.DENY
            and result.rule_name == "implicit-deny"
            and raw_tool_name.startswith("mcp__")
            and not raw_tool_name.startswith("mcp__harness-python-")
            and (raw_tool_name in declared_tools or tool_name in declared_tools)
        ):
            result = PolicyResult(
                decision=PolicyDecision.ALLOW,
                rule_name="published-mcp-tool",
                reason=("matched MCP tool declared by the published Agent tool directory"),
            )
        if (
            result.decision is PolicyDecision.DENY
            and raw_tool_name.startswith("mcp__harness-python-")
            and raw_tool_name in declared_tools
            and context.sandbox_command_executor is not None
        ):
            result = PolicyResult(
                decision=PolicyDecision.ALLOW,
                rule_name="declared-sandbox-python-tool",
                reason="matched declared Bundle Python tool in isolated Sandbox",
            )

        if result.decision is PolicyDecision.DENY:
            await self._append_denied(context, tool_call_id, result.reason)
            return _hook_output("deny", result.reason)

        human_approved = False
        if result.decision is PolicyDecision.ASK:
            approval = await self._approvals.request(
                tenant_id=context.run.tenant_id,
                run_id=context.run.run_id,
                tool_call_id=tool_call_id,
                reason=result.reason,
                message_id=context.assistant_message_id,
                inline=True,
                tool_name=tool_name,
                argument_summary=_approval_argument_summary(arguments),
                sandbox_provider=context.sandbox_provider,
                sandbox_isolation=context.sandbox_isolation.value,
                policy_rule=result.rule_name,
                risk=_approval_risk(tool_name),
            )
            try:
                decision = await self._approvals.wait_for_decision(approval.approval_id)
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._approvals.cancel_pending(
                        tenant_id=context.run.tenant_id,
                        approval_id=approval.approval_id,
                        reason="tool authorization wait interrupted",
                    )
                )
                raise
            if decision is not ApprovalStatus.APPROVED:
                return _hook_output("deny", "tool use was not approved")
            human_approved = True

        if tool_name == "Write" and write_target is not None:
            file_capabilities.note_authorized_write(tool_call_id, write_target)

        if self._quotas is not None:
            try:
                if raw_tool_name.startswith("mcp__"):
                    await self._quotas.consume(
                        tenant_id=context.run.tenant_id,
                        resource=QuotaResource.MCP_REQUESTS,
                        amount=1,
                        subject_id=context.run.run_id,
                        idempotency_key=(f"run:{context.run.run_id}:mcp:{tool_call_id}"),
                        agent_name=context.session.agent_name,
                        environment=context.session.environment,
                    )
                elif tool_name in {"Task", "Agent"}:
                    await self._quotas.reserve(
                        tenant_id=context.run.tenant_id,
                        resource=QuotaResource.CONCURRENT_SUBAGENTS,
                        amount=1,
                        subject_id=context.run.run_id,
                        idempotency_key=(f"run:{context.run.run_id}:subagent:{tool_call_id}"),
                        agent_name=context.session.agent_name,
                        environment=context.session.environment,
                        ttl_seconds=3600,
                    )
            except QuotaExceededError as error:
                reason = f"quota exceeded for {error.resource.value}"
                await self._append_denied(context, tool_call_id, reason)
                return _hook_output("deny", reason)

        await self._events.append(
            tenant_id=context.run.tenant_id,
            run_id=context.run.run_id,
            session_id=context.run.session_id,
            event_type="tool.allowed",
            payload={
                "tool_call_id": tool_call_id,
                "permission_stage": (
                    "sdk-auto"
                    if delegate_allowed_to_sdk_permissions and not human_approved
                    else "harness-final"
                ),
            },
        )
        return _hook_output(
            None if delegate_allowed_to_sdk_permissions and not human_approved else "allow",
            result.reason,
            updated_input=updated_arguments,
        )

    async def _append_denied(
        self,
        context: RuntimeContext,
        tool_call_id: str,
        reason: str,
    ) -> None:
        await self._events.append(
            tenant_id=context.run.tenant_id,
            run_id=context.run.run_id,
            session_id=context.run.session_id,
            event_type="tool.result",
            payload={
                "tool_call_id": tool_call_id,
                "is_error": True,
                "error": {"code": "policy_denied", "message": reason},
            },
        )
