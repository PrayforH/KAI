"""Capture project Skills once; Workers replay content-addressed Run snapshots."""

from typing import Any, Protocol, cast

from harness.core.manifest import SkillSnapshot
from harness.core.models import AgentRuntimeType
from harness.studio.platform_skills import skill_snapshots_for_references


class SkillPayloadStore(Protocol):
    async def transform(
        self, tenant_id: str, payload: dict[str, Any], *, inline: bool = False
    ) -> dict[str, Any]: ...


class ProjectSkillSnapshots:
    def __init__(self, blobs: SkillPayloadStore) -> None:
        self._blobs = blobs

    async def capture(
        self, tenant_id: str, references: tuple[str, ...], runtime: AgentRuntimeType
    ) -> list[dict[str, Any]]:
        snapshots = skill_snapshots_for_references(references, runtime, strict=True)
        return await self._transform(
            tenant_id, [snapshot.model_dump(mode="json") for snapshot in snapshots]
        )

    async def restore(
        self, tenant_id: str, payload: list[dict[str, Any]]
    ) -> tuple[SkillSnapshot, ...]:
        snapshots = await self._transform(tenant_id, payload, inline=True)
        return tuple(SkillSnapshot.model_validate(snapshot) for snapshot in snapshots)

    async def _transform(
        self, tenant_id: str, snapshots: list[dict[str, Any]], *, inline: bool = False
    ) -> list[dict[str, Any]]:
        payload = await self._blobs.transform(
            tenant_id, {"snapshot": {"skill_snapshots": snapshots}}, inline=inline
        )
        return cast(list[dict[str, Any]], payload["snapshot"]["skill_snapshots"])
