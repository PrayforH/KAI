"""Per-run platform tools, kept outside published agent configuration."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server


@dataclass(frozen=True)
class PlatformTool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class PlatformToolOverlay:
    tools: tuple[PlatformTool, ...] = ()
    instructions: str = ""

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
