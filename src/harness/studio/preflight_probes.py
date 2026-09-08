"""Real and deterministic probes used by Studio live Preflight."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, Protocol, cast

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from harness.core.manifest import AgentManifest
from harness.core.models import ExecutionIdentity, ModelCompatibility
from harness.runtime.cc_switch import CcSwitchClaudeConfig
from harness.runtime.mcp_credentials import McpCredentialError
from harness.runtime.tools import ToolResolutionError, ToolResolver
from harness.sandbox.base import SandboxHandle, SandboxProvider
from harness.studio.model_configuration import ModelConfigurationService


class PreflightCheckError(RuntimeError):
    """Stable, public failure fact; the underlying exception is never persisted."""

    def __init__(self, error_code: str, summary: str) -> None:
        self.error_code = error_code
        self.summary = summary
        super().__init__(summary)


@dataclass(frozen=True)
class PreflightEvidence:
    summary: str
    details: Mapping[str, str | int | bool]
    skipped: bool = False


class ModelPreflightProbe(Protocol):
    async def verify(
        self,
        tenant_id: str,
        manifest: AgentManifest,
        sandbox: SandboxProvider,
        handle: SandboxHandle,
    ) -> PreflightEvidence: ...


class McpPreflightProbe(Protocol):
    async def verify(
        self,
        manifest: AgentManifest,
        identity: ExecutionIdentity,
        sandbox: SandboxProvider,
        handle: SandboxHandle,
    ) -> PreflightEvidence: ...


class FakeModelPreflightProbe:
    def __init__(self, *, fail_code: str | None = None, delay_seconds: float = 0) -> None:
        self._fail_code = fail_code
        self._delay_seconds = delay_seconds

    async def verify(
        self,
        tenant_id: str,
        manifest: AgentManifest,
        sandbox: SandboxProvider,
        handle: SandboxHandle,
    ) -> PreflightEvidence:
        del tenant_id, manifest, sandbox, handle
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if self._fail_code is not None:
            raise PreflightCheckError(self._fail_code, "Model Preflight failed")
        return PreflightEvidence(
            summary="Model streaming and forced tool use passed",
            details={"streaming": True, "toolUse": True, "mode": "fake"},
        )


class FakeMcpPreflightProbe:
    def __init__(self, *, fail_code: str | None = None) -> None:
        self._fail_code = fail_code

    async def verify(
        self,
        manifest: AgentManifest,
        identity: ExecutionIdentity,
        sandbox: SandboxProvider,
        handle: SandboxHandle,
    ) -> PreflightEvidence:
        del identity, sandbox, handle
        if self._fail_code is not None:
            raise PreflightCheckError(self._fail_code, "MCP Preflight failed")
        count = sum(tool.mcp is not None for tool in manifest.spec.tools)
        return PreflightEvidence(
            summary=("No MCP declared" if count == 0 else "MCP initialize and tools/list passed"),
            details={"serverCount": count, "mode": "fake"},
            skipped=count == 0,
        )


def _messages_endpoint(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    return normalized if normalized.endswith("/v1/messages") else f"{normalized}/v1/messages"


def _stream_events(payload: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in payload.splitlines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        try:
            decoded: object = json.loads(data)
        except json.JSONDecodeError as error:
            raise PreflightCheckError(
                "model_stream_invalid", "Model returned an invalid SSE payload"
            ) from error
        if isinstance(decoded, dict):
            events.append(cast(dict[str, object], decoded))
    return events


class AnthropicSandboxModelProbe:
    """Force a harmless tool call through the model endpoint from the target Sandbox."""

    def __init__(
        self,
        config: CcSwitchClaudeConfig | Sequence[CcSwitchClaudeConfig],
        *,
        timeout_seconds: float = 45,
    ) -> None:
        self._configs = (config,) if isinstance(config, CcSwitchClaudeConfig) else tuple(config)
        if not self._configs:
            raise ValueError("At least one model route is required")
        self._timeout_seconds = timeout_seconds

    async def verify(
        self,
        tenant_id: str,
        manifest: AgentManifest,
        sandbox: SandboxProvider,
        handle: SandboxHandle,
    ) -> PreflightEvidence:
        del tenant_id
        config = next(
            (item for item in self._configs if item.route_id == manifest.spec.model.route),
            self._configs[0],
        )
        if config.compatibility is ModelCompatibility.UNSUPPORTED:
            raise PreflightCheckError(
                "model_incompatible", "Configured model route is not SDK compatible"
            )
        required = {"streaming", "tool_use"}
        if not required.issubset(config.capabilities):
            raise PreflightCheckError(
                "model_capability_mismatch",
                "Configured model route does not declare streaming and tool_use",
            )
        request = json.dumps(
            {
                "model": config.model,
                "max_tokens": 64,
                "stream": True,
                # Forced tool selection is a deterministic baseline probe. DeepSeek V4
                # enables thinking by default but rejects tool_choice in that mode.
                "thinking": {"type": "disabled"},
                "messages": [
                    {
                        "role": "user",
                        "content": "Call preflight_echo once with value ready.",
                    }
                ],
                "tools": [
                    {
                        "name": "preflight_echo",
                        "description": "Harmless capability compatibility check",
                        "input_schema": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                        },
                    }
                ],
                "tool_choice": {"type": "tool", "name": "preflight_echo"},
            },
            separators=(",", ":"),
        )
        credential = config.credential.get_secret_value()
        authorization = (
            f"Bearer {credential}" if config.resolved_auth_scheme == "bearer" else credential
        )
        command = (
            "set -eu; "
            "curl --no-progress-meter --fail-with-body --no-buffer "
            '--max-time "$HARNESS_PREFLIGHT_TIMEOUT" '
            '--request POST "$HARNESS_PREFLIGHT_MODEL_URL" '
            "--header 'content-type: application/json' "
            "--header 'anthropic-version: 2023-06-01' "
            '--header "$HARNESS_PREFLIGHT_AUTH_HEADER" '
            '--data-binary "$HARNESS_PREFLIGHT_REQUEST"'
        )
        header = (
            f"authorization: {authorization}"
            if config.resolved_auth_scheme == "bearer"
            else f"x-api-key: {authorization}"
        )
        result = await sandbox.execute(
            handle,
            ("bash", "-lc", command),
            environment={
                "HARNESS_PREFLIGHT_MODEL_URL": _messages_endpoint(config.base_url),
                "HARNESS_PREFLIGHT_AUTH_HEADER": header,
                "HARNESS_PREFLIGHT_REQUEST": request,
                "HARNESS_PREFLIGHT_TIMEOUT": str(int(self._timeout_seconds)),
            },
            timeout_seconds=self._timeout_seconds + 5,
        )
        if result.exit_code == 127:
            raise PreflightCheckError(
                "model_probe_dependency_missing",
                "Target Sandbox is missing the curl executable required by Model Preflight",
            )
        if result.exit_code != 0:
            raise PreflightCheckError(
                "model_unreachable", "Model endpoint could not complete the streaming probe"
            )
        if len(result.stdout.encode("utf-8")) > 2 * 1024 * 1024:
            raise PreflightCheckError(
                "model_stream_too_large", "Model Preflight response exceeded the safe limit"
            )
        events = _stream_events(result.stdout)
        event_types = {str(event.get("type", "")) for event in events}
        tool_use = any(
            event.get("type") == "content_block_start"
            and isinstance(event.get("content_block"), dict)
            and cast(dict[str, object], event["content_block"]).get("type") == "tool_use"
            for event in events
        )
        if len(events) < 2 or not {"message_start", "message_stop"}.issubset(event_types):
            raise PreflightCheckError(
                "model_streaming_unsupported", "Model did not return a complete SSE stream"
            )
        if not tool_use:
            raise PreflightCheckError(
                "model_tool_use_unsupported", "Model did not emit the forced tool call"
            )
        return PreflightEvidence(
            summary="Model streaming and forced tool use passed",
            details={
                "route": manifest.spec.model.route,
                "provider": config.provider,
                "streaming": True,
                "toolUse": True,
            },
        )


class ControlPlaneModelPreflightProbe:
    """Resolve the tenant model at probe time instead of reading deployment env."""

    def __init__(
        self,
        models: ModelConfigurationService,
        *,
        timeout_seconds: float = 45,
    ) -> None:
        self._models = models
        self._timeout_seconds = timeout_seconds

    async def verify(
        self,
        tenant_id: str,
        manifest: AgentManifest,
        sandbox: SandboxProvider,
        handle: SandboxHandle,
    ) -> PreflightEvidence:
        config = await self._models.resolve_runtime(
            tenant_id,
            manifest.metadata.name,
            manifest.spec.model.route,
        )
        if config is None:
            raise PreflightCheckError(
                "model_control_plane_unavailable",
                f"Model route is not configured in model management: {manifest.spec.model.route}",
            )
        return await AnthropicSandboxModelProbe(
            config,
            timeout_seconds=self._timeout_seconds,
        ).verify(tenant_id, manifest, sandbox, handle)


def _curl_config(url: str, headers: Mapping[str, str]) -> str:
    values = [url, *headers.values()]
    if any("\n" in value or "\r" in value for value in values):
        raise PreflightCheckError("mcp_config_invalid", "MCP HTTP config contains a newline")

    def quote(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    lines = [f'url = "{quote(url)}"']
    lines.extend(f'header = "{quote(name)}: {quote(value)}"' for name, value in headers.items())
    return "\n".join(lines)


class StreamableHttpMcpProbe:
    """Validate target-network reachability, MCP handshake, tools and a reviewed smoke."""

    def __init__(self, resolver: ToolResolver, *, timeout_seconds: float = 30) -> None:
        self._resolver = resolver
        self._timeout_seconds = timeout_seconds

    async def verify(
        self,
        manifest: AgentManifest,
        identity: ExecutionIdentity,
        sandbox: SandboxProvider,
        handle: SandboxHandle,
    ) -> PreflightEvidence:
        try:
            resolved = await self._resolver.resolve(manifest, identity)
        except McpCredentialError as error:
            raise PreflightCheckError(
                "mcp_credentials_unavailable",
                "Required MCP credentials could not be injected",
            ) from error
        except ToolResolutionError as error:
            raise PreflightCheckError(
                "mcp_configuration_invalid", "MCP registration could not be resolved"
            ) from error
        if not resolved.mcp_servers:
            return PreflightEvidence(
                summary="No MCP declared",
                details={"serverCount": 0},
                skipped=True,
            )
        total_tools = 0
        for server_name, raw_config in resolved.mcp_servers.items():
            config = cast(dict[str, object], raw_config)
            transport = config.get("type")
            if transport not in {"http", "sse"} or not isinstance(config.get("url"), str):
                raise PreflightCheckError(
                    "mcp_transport_unsupported",
                    "Preview Preflight requires an authenticated HTTP or SSE MCP server",
                )
            url = cast(str, config["url"])
            raw_headers = config.get("headers", {})
            headers = (
                {
                    str(key): str(value)
                    for key, value in cast(dict[object, object], raw_headers).items()
                }
                if isinstance(raw_headers, dict)
                else {}
            )
            await self._target_reachability(sandbox, handle, url, headers)
            try:
                async with self._client_streams(
                    cast(Literal["http", "sse"], transport),
                    url,
                    headers,
                ) as (read_stream, write_stream):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=self._timeout_seconds),
                    ) as session:
                        await session.initialize()
                        listed = await session.list_tools()
                        actual = {tool.name for tool in listed.tools}
                        prefix = f"mcp__{server_name}__"
                        expected = {
                            tool.removeprefix(prefix)
                            for tool in resolved.allowed_tools
                            if tool.startswith(prefix)
                        }
                        if not expected.issubset(actual):
                            raise PreflightCheckError(
                                "mcp_tool_mismatch",
                                "MCP tools/list does not contain every reviewed tool",
                            )
                        smoke = resolved.mcp_smokes.get(server_name)
                        if smoke is not None:
                            if smoke.tool not in actual:
                                raise PreflightCheckError(
                                    "mcp_tool_mismatch",
                                    "Reviewed MCP smoke tool is unavailable",
                                )
                            smoke_result = await session.call_tool(
                                smoke.tool, dict(smoke.arguments)
                            )
                            if smoke_result.isError:
                                raise PreflightCheckError(
                                    "mcp_smoke_failed",
                                    "MCP read-only smoke call returned an error",
                                )
                        total_tools += len(actual)
            except PreflightCheckError:
                raise
            except Exception as error:
                raise PreflightCheckError(
                    "mcp_unreachable", "MCP initialize or tools/list failed"
                ) from error
        return PreflightEvidence(
            summary="MCP initialize and tools/list passed",
            details={"serverCount": len(resolved.mcp_servers), "toolCount": total_tools},
        )

    @asynccontextmanager
    async def _client_streams(
        self,
        transport: Literal["http", "sse"],
        url: str,
        headers: Mapping[str, str],
    ) -> AsyncGenerator[tuple[Any, Any]]:
        if transport == "sse":
            async with sse_client(
                url,
                headers=dict(headers),
                timeout=self._timeout_seconds,
                sse_read_timeout=self._timeout_seconds,
            ) as streams:
                yield streams
            return
        async with httpx.AsyncClient(
            headers=dict(headers),
            timeout=httpx.Timeout(self._timeout_seconds),
            trust_env=False,
        ) as client:
            async with streamable_http_client(
                url,
                http_client=client,
            ) as (read_stream, write_stream, _session_id):
                yield read_stream, write_stream

    async def _target_reachability(
        self,
        sandbox: SandboxProvider,
        handle: SandboxHandle,
        url: str,
        headers: Mapping[str, str],
    ) -> None:
        ping = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "preflight",
                "method": "ping",
            },
            separators=(",", ":"),
        )
        command = (
            "set -eu; umask 077; mkdir -p .harness-preflight; "
            "config=.harness-preflight/mcp-curl.conf; "
            "trap 'rm -f -- \"$config\"' EXIT; "
            'printf \'%s\' "$HARNESS_PREFLIGHT_CURL_CONFIG" > "$config"; '
            'curl --config "$config" --no-progress-meter --silent --show-error '
            '--max-time "$HARNESS_PREFLIGHT_TIMEOUT" --request POST '
            "--header 'content-type: application/json' "
            "--header 'accept: application/json, text/event-stream' "
            '--data-binary "$HARNESS_PREFLIGHT_MCP_REQUEST" '
            "--output /dev/null --write-out '%{http_code}'"
        )
        result = await sandbox.execute(
            handle,
            ("bash", "-lc", command),
            environment={
                "HARNESS_PREFLIGHT_CURL_CONFIG": _curl_config(url, headers),
                "HARNESS_PREFLIGHT_MCP_REQUEST": ping,
                "HARNESS_PREFLIGHT_TIMEOUT": str(int(self._timeout_seconds)),
            },
            timeout_seconds=self._timeout_seconds + 5,
        )
        if result.exit_code != 0:
            raise PreflightCheckError(
                "mcp_target_network_unreachable",
                "MCP endpoint is not reachable from the target Sandbox",
            )
        try:
            status = int(result.stdout.strip())
        except ValueError as error:
            raise PreflightCheckError(
                "mcp_target_response_invalid",
                "MCP endpoint returned an invalid target-network response",
            ) from error
        if status in {401, 403}:
            raise PreflightCheckError(
                "mcp_credentials_rejected",
                "MCP endpoint rejected the injected target credential",
            )
        if status == 404:
            raise PreflightCheckError("mcp_endpoint_not_found", "MCP endpoint path was not found")
        if status == 0 or status >= 500:
            raise PreflightCheckError(
                "mcp_target_unavailable",
                "MCP endpoint is unavailable from the target Sandbox",
            )
