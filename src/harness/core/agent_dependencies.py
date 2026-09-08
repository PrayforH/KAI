"""Dependency projection for immutable Agent snapshots."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True)
class AgentDependencies:
    mcp_references: tuple[str, ...] = ()
    knowledge_references: tuple[str, ...] = ()


def dependencies_from_snapshot(snapshot: Mapping[str, object]) -> AgentDependencies:
    """Read runtime dependencies from the serialized manifest in a Version snapshot."""

    manifest = snapshot.get("manifest")
    if not isinstance(manifest, Mapping):
        return AgentDependencies()
    spec = cast(Mapping[str, object], manifest).get("spec")
    if not isinstance(spec, Mapping):
        return AgentDependencies()

    mcp_references: list[str] = []
    tools = cast(Mapping[str, object], spec).get("tools")
    if isinstance(tools, (list, tuple)):
        for tool in cast(list[object] | tuple[object, ...], tools):
            if not isinstance(tool, Mapping):
                continue
            reference = cast(Mapping[str, object], tool).get("mcp")
            if isinstance(reference, str) and reference not in mcp_references:
                mcp_references.append(reference)

    knowledge_references: list[str] = []
    raw_knowledge = cast(Mapping[str, object], spec).get("knowledgeReferences")
    if isinstance(raw_knowledge, (list, tuple)):
        for reference in cast(list[object] | tuple[object, ...], raw_knowledge):
            if isinstance(reference, str) and reference not in knowledge_references:
                knowledge_references.append(reference)

    return AgentDependencies(
        mcp_references=tuple(mcp_references),
        knowledge_references=tuple(knowledge_references),
    )
