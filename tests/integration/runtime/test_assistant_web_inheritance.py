"""助理默认跟随平台联网：主助理与子助理都不必逐个重新发布带联网工具的智能体。"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions, TextBlock
from pydantic import SecretStr

from harness.config import Settings
from harness.core.manifest import (
    AgentManifestSnapshot,
    ToolDirectoryEntry,
    ToolDirectorySnapshot,
    load_manifest,
)
from harness.core.models import (
    AgentVersion,
    AgentVersionStatus,
    ModelCompatibility,
    ModelRoute,
    Run,
    RunStatus,
    Session,
)
from harness.knowledge.repositories import InMemoryKnowledgeRepository
from harness.knowledge.service import KnowledgeService
from harness.observability.provider import build_observability
from harness.runtime.base import RuntimeContext
from harness.runtime.claude_sdk import ClaudeSdkRuntime, ContextWindowObservation
from harness.runtime.tools import ToolResolver
from harness.runtime.web_tools import WEB_TOOL_NAMES
from harness.studio.mcp_credential_store import (
    InMemoryMcpCredentialRepository,
    McpCredentialCipher,
    McpCredentialService,
)
from harness.studio.web_configuration import ConfigureWebRequest, WebConfigurationService


async def _options(
    tmp_path: Path,
    *,
    assistant_scope: bool,
    personal_web_enabled: bool,
    with_directory: bool,
    agent_declares_web: bool = False,
) -> ClaudeAgentOptions:
    snapshot = load_manifest("tests/fixtures/agents/echo-agent/agent.yaml")
    if agent_declares_web:
        payload = snapshot.model_dump(mode="json")
        payload["manifest"]["spec"]["tools"] = [
            *payload["manifest"]["spec"].get("tools", []),
            {"builtin": "WebSearch", "mcp": None, "python_entry": None},
        ]
        snapshot = AgentManifestSnapshot.model_validate(payload)
    if with_directory:
        directory = ToolDirectorySnapshot.create(
            catalog_revision=1,
            exposure_mode=snapshot.manifest.spec.tool_exposure_mode,
            entries=tuple(
                ToolDirectoryEntry(
                    name=name, source="builtin", logicalReference=name,
                    description=name, risk="low", resultTrust="safe",
                )
                for name in (
                    tool.builtin for tool in snapshot.manifest.spec.tools
                    if tool.builtin is not None
                )
            ),
        )
        snapshot = AgentManifestSnapshot.model_validate(
            snapshot.model_dump() | {"tool_directory": directory.model_dump(mode="json")}
        )
    captured: list[ClaudeAgentOptions] = []

    async def fake_query(prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[object]:
        captured.append(options)
        yield ContextWindowObservation(
            phase="before", total_tokens=1_000, max_tokens=180_000, raw_max_tokens=200_000,
            percentage=0.56, model=options.model or "", auto_compact_enabled=True,
            auto_compact_threshold=175_000, categories=(("Messages", 1_000),),
        )
        yield TextBlock(text="fake response")

    web = WebConfigurationService(
        McpCredentialService(
            InMemoryMcpCredentialRepository(), McpCredentialCipher(SecretStr("test-key"))
        ),
        api_key="platform-key",
    )
    await web.configure("tenant-a", "user-1", ConfigureWebRequest(enabled=personal_web_enabled))
    version = AgentVersion(
        tenant_id="tenant-a",
        owner_user_id="user-1",
        name="echo-agent",
        version="0.1.0",
        status=AgentVersionStatus.PUBLISHED,
        manifest_hash=snapshot.content_hash,
        snapshot=snapshot.model_dump(mode="json"),
        created_at=datetime.now(UTC),
    )
    runtime = ClaudeSdkRuntime(
        tool_resolver=ToolResolver(web_enabled=True, web_configurations=web),
        agent_version=version,
        routes=[
            ModelRoute(
                route_id="new-api-default", provider="new-api",
                base_url="https://new-api.example/v1", model="claude-sonnet-4-6",
                compatibility=ModelCompatibility.FULL,
                capabilities=frozenset({"streaming", "tool_use"}),
            )
        ],
        route_secrets={"new-api-default": "super-secret"},
        query_factory=fake_query,
        observability=build_observability(
            Settings(
                otel_enabled=False, otlp_endpoint="http://unused/v1/traces",
                otel_content_capture="redacted",
            )
        ),
        knowledge=KnowledgeService(InMemoryKnowledgeRepository()),
    )
    now = datetime.now(UTC)
    run_input: dict[str, object] = {"prompt": "hello"}
    if assistant_scope:
        run_input["assistant_scope"] = True
    context = RuntimeContext(
        run=Run(
            run_id="run-1", session_id="session-1", tenant_id="tenant-a",
            status=RunStatus.RUNNING, idempotency_key="idem-1",
            created_at=now, updated_at=now, input=run_input,
        ),
        session=Session(
            session_id="session-1", tenant_id="tenant-a", user_id="user-1",
            agent_name="echo-agent", agent_version="0.1.0",
            created_at=now, knowledge_snapshot_bindings=(),
        ),
        workspace=tmp_path,
        context_projection="",
    )
    async for _ in runtime.execute(context):
        pass
    assert captured, "runtime produced no SDK options"
    return captured[0]


@pytest.mark.asyncio
async def test_assistant_scope_inherits_platform_web_without_republishing(tmp_path: Path) -> None:
    """助理面默认跟随平台联网：智能体没声明联网工具也能搜索/抓取。"""
    options = await _options(
        tmp_path, assistant_scope=True, personal_web_enabled=True, with_directory=False
    )
    assert "harness-web" in options.mcp_servers
    assert WEB_TOOL_NAMES["WebSearch"] in options.allowed_tools
    assert WEB_TOOL_NAMES["WebFetch"] in options.allowed_tools
    # 联网工具走 MCP 暴露，不进内置工具清单。
    assert "WebSearch" not in (options.tools or [])


@pytest.mark.asyncio
async def test_assistant_scope_keeps_a_published_tool_directory_intact(tmp_path: Path) -> None:
    """已发布工具目录是契约：注入联网工具不得触发目录校验失败。"""
    options = await _options(
        tmp_path, assistant_scope=True, personal_web_enabled=True, with_directory=True
    )
    assert "harness-web" in options.mcp_servers
    assert WEB_TOOL_NAMES["WebSearch"] in options.allowed_tools


@pytest.mark.asyncio
async def test_task_surface_does_not_inherit_platform_web(tmp_path: Path) -> None:
    """普通任务/项目任务保持各自范围，不被助理的联网默认污染。"""
    options = await _options(
        tmp_path, assistant_scope=False, personal_web_enabled=True, with_directory=False
    )
    assert "harness-web" not in options.mcp_servers


@pytest.mark.asyncio
async def test_user_switch_off_is_respected_for_assistants(tmp_path: Path) -> None:
    """用户关掉联网后，助理也不再装配联网工具。"""
    options = await _options(
        tmp_path, assistant_scope=True, personal_web_enabled=False, with_directory=False
    )
    assert "harness-web" not in options.mcp_servers


@pytest.mark.asyncio
async def test_assistant_scope_describes_available_web_capabilities(tmp_path: Path) -> None:
    """联网继承与工具事实不要求特定业务步骤。"""
    from harness.runtime.web_tools import WEB_ASSISTANT_CONTRACT

    options = await _options(
        tmp_path, assistant_scope=True, personal_web_enabled=True, with_directory=False
    )
    assert WEB_ASSISTANT_CONTRACT in (options.system_prompt or "")
    assert "mcp__harness-web__search" in (options.system_prompt or "")


@pytest.mark.asyncio
async def test_task_surface_keeps_the_plain_web_contract(tmp_path: Path) -> None:
    from harness.runtime.web_tools import WEB_ASSISTANT_CONTRACT

    options = await _options(
        tmp_path, assistant_scope=False, personal_web_enabled=True, with_directory=False
    )
    assert WEB_ASSISTANT_CONTRACT not in (options.system_prompt or "")


@pytest.mark.asyncio
async def test_contract_applies_when_the_agent_already_declares_web(tmp_path: Path) -> None:
    """声明与继承的工具具有相同的能力说明。"""
    from harness.runtime.web_tools import WEB_ASSISTANT_CONTRACT

    options = await _options(
        tmp_path,
        assistant_scope=True,
        personal_web_enabled=True,
        with_directory=False,
        agent_declares_web=True,
    )
    assert WEB_ASSISTANT_CONTRACT in (options.system_prompt or "")


@pytest.mark.asyncio
async def test_assistant_web_capability_facts_do_not_impose_a_business_workflow(
    tmp_path: Path,
) -> None:
    """能力描述不得夹带天气关键词、必经步骤或角色优先级补丁。"""
    from harness.runtime.web_tools import WEB_ASSISTANT_CONTRACT

    options = await _options(
        tmp_path, assistant_scope=True, personal_web_enabled=True, with_directory=False
    )
    prompt = options.system_prompt or ""
    assert prompt.startswith(WEB_ASSISTANT_CONTRACT)
    assert "公开联网不扩大权限" in prompt
    assert "天气" not in WEB_ASSISTANT_CONTRACT
    assert "先检索" not in WEB_ASSISTANT_CONTRACT
    assert "是否使用" in WEB_ASSISTANT_CONTRACT
