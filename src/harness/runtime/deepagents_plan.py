"""The single derivation that the code view, the project export and the runtime share.

An Agent Studio draft that selects the ``deepagents`` runtime has three consumers:

* the read-only **code view** and the **project export**, which render the plan as
  installable Python source (``harness.studio.deepagents_export``);
* the **runtime**, which materializes the plan as a live LangGraph graph.

If each consumer derived its own tool set, permission mode or recursion budget,
the source a user reads would stop being the source the platform executes. This
module is deliberately free of third-party imports and of I/O: it only turns
declared Agent facts into the concrete shape both renderers consume.

``provider:model`` resolution and the builtin-to-filesystem mapping are pinned to
``deepagents==0.7.13``; the mapping is documented in the export module.
"""

from __future__ import annotations

from dataclasses import dataclass

DEEPAGENTS_PINNED_VERSION = "0.7.13"

# Platform builtin -> DeepAgents filesystem tool. `delete` is intentionally
# absent: the platform has no delete builtin, and withholding it from the
# FilesystemMiddleware tool list is how the export keeps that true.
BUILTIN_TO_FILESYSTEM_TOOL = {
    "Read": "read_file",
    "Glob": "glob",
    "Grep": "grep",
    "Write": "write_file",
    "Edit": "edit_file",
    "Bash": "execute",
}

# Always exposed so the skills progressive-disclosure loop (ls -> read_file)
# stays usable; declared as export additions because the platform capability
# catalog has no matching entries.
ALWAYS_ON_FILESYSTEM_TOOLS = ("ls", "read_file")

# DeepAgents filesystem tool -> the platform builtin whose policy posture it
# inherits. Policy rules, quota resources and the write-containment check are
# all written against platform builtin names, so the runtime tool gate must
# translate back before it evaluates anything; otherwise a rule on ``Bash``
# would never match DeepAgents' ``execute``.
#
# ``ls`` has no platform counterpart. It is mapped onto ``Glob`` because the
# platform's policy vocabulary models read-only workspace discovery as one
# capability, and both tools are bounded to listing/finding inside the
# workspace.
FILESYSTEM_TOOL_TO_BUILTIN = {
    **{tool: builtin for builtin, tool in BUILTIN_TO_FILESYSTEM_TOOL.items()},
    "ls": "Glob",
}

# Platform builtins with no DeepAgents counterpart and no platform-side
# equivalent; the compiler rejects them before a draft can be published.
UNMAPPED_BUILTIN_TOOLS = frozenset({"Task", "Agent"})

API_FORMAT_PROVIDER = {
    "anthropic_compatible": "anthropic",
    "openai_compatible": "openai",
}

# Fallback prefix heuristic, used only when the route catalog carries no
# apiFormat. Platform route models are often aliases (`deepseek-v4-pro`) that
# say nothing about the wire protocol.
MODEL_PROVIDER_PREFIXES = (
    ("claude", "anthropic"),
    ("gemini", "google_genai"),
)

_DEFAULT_RECURSION_FLOOR = 50
# The exporter maps maxTurns onto LangGraph's recursion limit; each turn costs
# two graph steps (model + tools), so the budget is doubled.
_RECURSION_STEPS_PER_TURN = 2
_MAX_SHELL_TIMEOUT_SECONDS = 3_600


@dataclass(frozen=True)
class DeepagentsPlan:
    """Concrete DeepAgents shape derived from declared Agent facts."""

    model: str
    provider: str
    filesystem_tools: tuple[str, ...]
    added_export_tools: tuple[str, ...]
    unmapped_builtin_tools: tuple[str, ...]
    has_bash: bool
    read_only: bool
    permissions: bool
    shell_timeout: int
    recursion_limit: int
    with_mcp: bool
    with_skills: bool


def deepagents_model(model: str, api_format: str | None = None) -> tuple[str, str]:
    """Resolve the DeepAgents ``provider:model`` string and its provider name.

    The platform route's ``apiFormat`` is authoritative when present, because
    route model names are aliases that do not describe the wire protocol.
    """

    provider = API_FORMAT_PROVIDER.get(api_format or "")
    if provider is None:
        lowered = model.lower()
        provider = next(
            (name for prefix, name in MODEL_PROVIDER_PREFIXES if lowered.startswith(prefix)),
            "openai",
        )
    return f"{provider}:{model}", provider


def build_deepagents_plan(
    *,
    builtin_tools: tuple[str, ...],
    permission_policy: str,
    model: str,
    api_format: str | None = None,
    max_turns: int | None = None,
    timeout_seconds: float | None = None,
    with_mcp: bool = False,
    with_skills: bool = False,
) -> DeepagentsPlan:
    """Derive the one plan that the code view, the export and the runtime share."""

    has_bash = "Bash" in builtin_tools
    filesystem_tools: list[str] = list(ALWAYS_ON_FILESYSTEM_TOOLS)
    selected: set[str] = set()
    for builtin in builtin_tools:
        mapped = BUILTIN_TO_FILESYSTEM_TOOL.get(builtin)
        if mapped is None:
            continue
        selected.add(mapped)
        if mapped not in filesystem_tools:
            filesystem_tools.append(mapped)
    added = tuple(tool for tool in ALWAYS_ON_FILESYSTEM_TOOLS if tool not in selected)

    read_only = permission_policy == "production-read-only"
    # 0.7.13 constraint: FilesystemPermission requires a backend without command
    # execution, so workspace permissions survive only on the no-Bash branch.
    permissions = read_only and not has_bash

    shell_timeout = min(int(timeout_seconds or 300), _MAX_SHELL_TIMEOUT_SECONDS)
    resolved_model, provider = deepagents_model(model, api_format)
    return DeepagentsPlan(
        model=resolved_model,
        provider=provider,
        filesystem_tools=tuple(filesystem_tools),
        added_export_tools=added,
        unmapped_builtin_tools=tuple(
            name for name in builtin_tools if name in UNMAPPED_BUILTIN_TOOLS
        ),
        has_bash=has_bash,
        read_only=read_only,
        permissions=permissions,
        shell_timeout=shell_timeout,
        recursion_limit=max(
            (max_turns or 100) * _RECURSION_STEPS_PER_TURN,
            _DEFAULT_RECURSION_FLOOR,
        ),
        with_mcp=with_mcp,
        with_skills=with_skills,
    )
