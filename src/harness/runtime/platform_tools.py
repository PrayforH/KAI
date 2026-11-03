"""Per-run platform tools, kept outside published agent configuration."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server


@dataclass(frozen=True)
class PlatformTool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@runtime_checkable
class ToolWorkflow(Protocol):
    """Per-run workflow admission, in addition to normal tool authorization."""

    def before_tool(self, name: str) -> str | None: ...

    def before_finish(self) -> str | None: ...


@dataclass(frozen=True)
class PlatformToolOverlay:
    tools: tuple[PlatformTool, ...] = ()
    instructions: str = ""
    # Server-resolved Bot roles may execute without a mandatory acknowledgement.
    # This is presentation guidance, never a tool permission or client override.
    require_public_preface: bool = True
    workflow: ToolWorkflow | None = None
    system_prefix: str = ""

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(f"mcp__harness-builder__{tool.name}" for tool in self.tools)

    def sdk_server(self):
        return create_sdk_mcp_server(
            name="harness-builder",
            version="1.0.0",
            tools=[
                SdkMcpTool(
                    name=t.name, description=t.description, input_schema=t.schema, handler=t.handler
                )
                for t in self.tools
            ],
        )
