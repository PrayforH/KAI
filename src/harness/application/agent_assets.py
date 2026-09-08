"""Stage immutable main and subagent runtime assets from the registry."""

import json
import shutil
from pathlib import Path

from harness.core.errors import ConflictError
from harness.core.manifest import (
    AgentManifestSnapshot,
    ManifestValidationError,
    materialize_python_tool_snapshot_set,
    materialize_skill_snapshot_set,
)
from harness.core.models import AgentVersion, AgentVersionStatus
from harness.core.ports import AgentRegistry
from harness.runtime.execution_contract import VISIBLE_EXECUTION_CONTRACT


def _codex_agent_file_name(alias: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in "-_" else "-" for character in alias
    )
    return f"harness-{safe or 'agent'}.toml"


def _materialize_codex_subagents(
    root: AgentManifestSnapshot,
    children: dict[str, AgentVersion],
    workspace: Path,
) -> None:
    """Expose pinned Studio roles through Codex's project-scoped agent files."""

    agent_root = workspace / ".codex" / "agents"
    agent_root.mkdir(parents=True, exist_ok=True)
    for stale in agent_root.glob("harness-*.toml"):
        stale.unlink()
    declarations = {
        declaration.runtime_name: declaration for declaration in root.manifest.spec.subagents
    }
    for alias, child in children.items():
        declaration = declarations[alias]
        snapshot = AgentManifestSnapshot.model_validate(child.snapshot)
        policy = snapshot.manifest.spec.permissions.policy
        sandbox_mode = "read-only" if policy == "production-read-only" else "workspace-write"
        developer_instructions = (
            f"{snapshot.system_prompt.rstrip()}\n\n{VISIBLE_EXECUTION_CONTRACT}"
        )
        description = declaration.description or f"Studio pinned role {alias}"
        content = "\n".join(
            (
                f"name = {json.dumps(alias, ensure_ascii=False)}",
                f"description = {json.dumps(description, ensure_ascii=False)}",
                f"sandbox_mode = {json.dumps(sandbox_mode)}",
                (
                    "developer_instructions = "
                    f"{json.dumps(developer_instructions, ensure_ascii=False)}"
                ),
                "",
            )
        )
        (agent_root / _codex_agent_file_name(alias)).write_text(content, encoding="utf-8")


def _materialize_codex_skills(workspace: Path, names: tuple[str, ...]) -> None:
    """Mirror immutable bundle Skills into Codex's repository discovery root.

    ``.claude/skills`` remains the compatibility and tool-gate source of truth.
    Codex discovers repository Skills from ``.agents/skills``; harness-owned
    mirrors use a prefix so existing user-authored repository Skills are left
    untouched.
    """

    source_root = workspace / ".claude" / "skills"
    codex_root = workspace / ".agents" / "skills"
    codex_root.mkdir(parents=True, exist_ok=True)
    for stale in codex_root.glob("harness-*"):
        if stale.is_symlink() or stale.is_file():
            stale.unlink()
        else:
            shutil.rmtree(stale)
    for name in names:
        source = source_root / name
        target = codex_root / f"harness-{name}"
        shutil.copytree(source, target)


async def resolve_published_agent_versions(
    registry: AgentRegistry,
    *,
    tenant_id: str,
    owner_user_id: str,
    agent_name: str,
    agent_version: str,
    allow_validated_graph: bool = False,
) -> tuple[AgentVersion, dict[str, AgentVersion]]:
    """Resolve a fixed one-level SDK delegation graph and enforce release state.

    Studio Try Runs may execute an immutable validated graph. Normal sessions
    keep the default publication-only boundary.
    """
    root = await registry.get(tenant_id, owner_user_id, agent_name, agent_version)
    if root.status is not AgentVersionStatus.PUBLISHED and not (
        allow_validated_graph and root.status is AgentVersionStatus.VALIDATED
    ):
        raise ConflictError("sessions can only use a published Agent version")
    snapshot = AgentManifestSnapshot.model_validate(root.snapshot)
    children: dict[str, AgentVersion] = {}
    for subagent in snapshot.manifest.spec.subagents:
        name, separator, version_id = subagent.ref.rpartition("@")
        if not separator or not name or not version_id:
            raise ManifestValidationError(
                f"subagent reference requires name@version: {subagent.ref}"
            )
        runtime_name = subagent.runtime_name
        if runtime_name in children:
            raise ManifestValidationError(f"duplicate subagent runtime name: {runtime_name}")
        child = await registry.get(tenant_id, owner_user_id, name, version_id)
        if child.status is not AgentVersionStatus.PUBLISHED and not (
            allow_validated_graph and child.status is AgentVersionStatus.VALIDATED
        ):
            raise ConflictError(f"subagent must be published before use: {name}@{version_id}")
        child_snapshot = AgentManifestSnapshot.model_validate(child.snapshot)
        if child_snapshot.manifest.spec.subagents:
            raise ManifestValidationError(
                f"nested subagent delegation is not supported: {runtime_name}"
            )
        children[runtime_name] = child
    return root, children


async def stage_published_agent_assets(
    registry: AgentRegistry,
    *,
    tenant_id: str,
    owner_user_id: str,
    agent_name: str,
    agent_version: str,
    workspace: Path,
    allow_validated_graph: bool = False,
) -> tuple[str, ...]:
    version, children = await resolve_published_agent_versions(
        registry,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        agent_name=agent_name,
        agent_version=agent_version,
        allow_validated_graph=allow_validated_graph,
    )
    snapshot = AgentManifestSnapshot.model_validate(version.snapshot)
    snapshots = [snapshot]
    for child in children.values():
        snapshots.append(AgentManifestSnapshot.model_validate(child.snapshot))
    materialize_python_tool_snapshot_set(snapshots, workspace)
    staged_skills = materialize_skill_snapshot_set(snapshots, workspace)
    if snapshot.manifest.spec.runtime == "codex-app-server":
        _materialize_codex_subagents(snapshot, children, workspace)
        _materialize_codex_skills(workspace, staged_skills)
    return staged_skills
