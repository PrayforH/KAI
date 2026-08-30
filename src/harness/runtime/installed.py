"""Runtime types installed in every composed worker and API process.

This tuple is the runtime-registration side of the RuntimeCapabilities
contract: tests verify it matches ``tests/fixtures/runtime/runtime_capabilities_v0.json``,
which is also the fixture the compiler conclusions and the Builder display
are validated against.
"""

from harness.core.models import AgentRuntimeType

INSTALLED_AGENT_RUNTIMES: tuple[AgentRuntimeType, ...] = (
    "claude-agent-sdk",
    "codex-app-server",
)
