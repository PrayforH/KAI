"""One derivation feeds the code view, the export and the runtime.

If these ever disagreed, the source a user reads in the console would stop being
the source the platform executes — which is the defect the DeepAgents runtime
exists to remove. So the plan is pinned here, independently of both renderers.
"""

from __future__ import annotations

from harness.runtime.deepagents_plan import (
    BUILTIN_TO_FILESYSTEM_TOOL,
    DEEPAGENTS_PINNED_VERSION,
    FILESYSTEM_TOOL_TO_BUILTIN,
    build_deepagents_plan,
    deepagents_model,
)


def test_delete_is_never_exposed() -> None:
    """The platform has no delete builtin, so the plan cannot offer one."""

    plan = build_deepagents_plan(
        builtin_tools=("Read", "Write", "Edit", "Glob", "Grep", "Bash"),
        permission_policy="default",
        model="m",
    )

    assert "delete" not in plan.filesystem_tools
    assert "delete" not in BUILTIN_TO_FILESYSTEM_TOOL.values()
    assert "delete" not in FILESYSTEM_TOOL_TO_BUILTIN


def test_always_on_tools_keep_skill_disclosure_working() -> None:
    """`ls` + `read_file` are what progressive Skill disclosure needs."""

    plan = build_deepagents_plan(
        builtin_tools=("Bash",),
        permission_policy="default",
        model="m",
    )

    assert plan.filesystem_tools == ("ls", "read_file", "execute")
    assert plan.added_export_tools == ("ls", "read_file")


def test_declared_builtins_are_mapped_once_and_in_order() -> None:
    plan = build_deepagents_plan(
        builtin_tools=("Read", "Glob", "Read", "Grep", "Write", "Edit"),
        permission_policy="default",
        model="m",
    )

    assert plan.filesystem_tools == (
        "ls",
        "read_file",
        "glob",
        "grep",
        "write_file",
        "edit_file",
    )
    # `Read` covers read_file; `ls` has no platform builtin, so it is always an
    # addition the export has to explain.
    assert plan.added_export_tools == ("ls",)


def test_unmapped_builtins_are_reported_not_silently_dropped() -> None:
    plan = build_deepagents_plan(
        builtin_tools=("Read", "Task"),
        permission_policy="default",
        model="m",
    )

    assert plan.unmapped_builtin_tools == ("Task",)


def test_read_only_policy_only_survives_without_bash() -> None:
    """0.7.13 rejects workspace permissions on a backend that can execute."""

    without_bash = build_deepagents_plan(
        builtin_tools=("Read", "Write"),
        permission_policy="production-read-only",
        model="m",
    )
    with_bash = build_deepagents_plan(
        builtin_tools=("Read", "Write", "Bash"),
        permission_policy="production-read-only",
        model="m",
    )

    assert without_bash.read_only is True
    assert without_bash.permissions is True
    assert with_bash.read_only is True
    assert with_bash.permissions is False


def test_recursion_budget_follows_the_manifest_turn_limit() -> None:
    default = build_deepagents_plan(builtin_tools=(), permission_policy="default", model="m")
    budgeted = build_deepagents_plan(
        builtin_tools=(),
        permission_policy="default",
        model="m",
        max_turns=200,
    )

    # Each turn costs a model step plus a tool step, with a floor for tiny drafts.
    assert default.recursion_limit == 200
    assert budgeted.recursion_limit == 400


def test_shell_timeout_is_bounded_by_the_manifest_timeout() -> None:
    plan = build_deepagents_plan(
        builtin_tools=("Bash",),
        permission_policy="default",
        model="m",
        timeout_seconds=45,
    )
    unbounded = build_deepagents_plan(
        builtin_tools=("Bash",),
        permission_policy="default",
        model="m",
    )

    assert plan.shell_timeout == 45
    assert unbounded.shell_timeout == 300


def test_route_protocol_decides_the_provider_not_the_model_name() -> None:
    """Platform route models are aliases and say nothing about the protocol."""

    assert deepagents_model("deepseek-v4-pro", "anthropic_compatible") == (
        "anthropic:deepseek-v4-pro",
        "anthropic",
    )
    assert deepagents_model("deepseek-v4-pro", "openai_compatible") == (
        "openai:deepseek-v4-pro",
        "openai",
    )
    # Without a catalog protocol, only an unmistakable prefix is trusted.
    assert deepagents_model("claude-sonnet-4") == ("anthropic:claude-sonnet-4", "anthropic")
    assert deepagents_model("deepseek-v4-pro") == ("openai:deepseek-v4-pro", "openai")


def test_the_reverse_mapping_covers_every_exposed_tool() -> None:
    """The runtime tool gate translates back before any policy runs."""

    for builtin, tool in BUILTIN_TO_FILESYSTEM_TOOL.items():
        assert FILESYSTEM_TOOL_TO_BUILTIN[tool] == builtin
    # `ls` has no platform builtin; it inherits read-only discovery.
    assert FILESYSTEM_TOOL_TO_BUILTIN["ls"] == "Glob"
    assert DEEPAGENTS_PINNED_VERSION == "0.7.13"
