"""User-owned Skills that become immutable Run snapshots only when enabled."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from harness.assistant.repository import PostgresAssistantRepository
from harness.assistant.skill_models import PersonalSkill, SkillProposal
from harness.core.manifest import SkillSnapshot
from harness.studio.models import DraftSkill
from harness.studio.platform_skills import authored_skill_snapshot
from harness.studio.skill_import import validate_authored_skill


class PersonalSkillService:
    def __init__(self, repository: PostgresAssistantRepository) -> None:
        self._repository = repository

    def with_repository(self, repository: PostgresAssistantRepository) -> PersonalSkillService:
        return PersonalSkillService(repository)

    async def list(self, tenant_id: str, user_id: str) -> list[PersonalSkill]:
        return await self._repository.list_personal_skills(tenant_id, user_id)

    async def get(self, tenant_id: str, user_id: str, skill_id: str) -> PersonalSkill:
        return await self._repository.personal_skill(tenant_id, user_id, skill_id)

    async def enabled_snapshots(
        self, tenant_id: str, user_id: str
    ) -> tuple[SkillSnapshot, ...]:
        """Pin the exact enabled revision so a later edit cannot change a live Run."""
        skills = await self._repository.list_personal_skills(
            tenant_id, user_id, enabled_only=True
        )
        return tuple(
            authored_skill_snapshot(
                skill.skill,
                source=f"personal-skill:{skill.skill_id}@{skill.revision}",
            )
            for skill in skills
        )

    async def save_draft(
        self,
        tenant_id: str,
        user_id: str,
        *,
        skill: DraftSkill,
        source_proposal: SkillProposal | None = None,
    ) -> PersonalSkill:
        validate_authored_skill(skill)
        existing = await self._repository.find_personal_skill_by_name(
            tenant_id, user_id, skill.name
        )
        if existing is not None:
            if (
                source_proposal is not None
                and existing.source_proposal_id == source_proposal.proposal_id
            ):
                # Concurrent confirmation already saved this proposal.
                return existing
            raise ValueError("A personal Skill with this name already exists")
        now = datetime.now(UTC)
        return await self._repository.create_personal_skill(
            PersonalSkill(
                skillId=f"pskill_{uuid4().hex}",
                tenantId=tenant_id,
                userId=user_id,
                name=skill.name,
                displayName=_display_name(skill),
                description=skill.description,
                enabled=False,
                revision=1,
                sourceProposalId=(
                    source_proposal.proposal_id if source_proposal is not None else None
                ),
                skill=skill,
                createdAt=now,
                updatedAt=now,
            )
        )

    async def set_enabled(
        self,
        tenant_id: str,
        user_id: str,
        skill_id: str,
        *,
        enabled: bool,
        expected_revision: int,
    ) -> PersonalSkill:
        return await self._repository.set_personal_skill_enabled(
            tenant_id,
            user_id,
            skill_id,
            enabled=enabled,
            expected_revision=expected_revision,
            now=datetime.now(UTC),
        )

    async def delete(self, tenant_id: str, user_id: str, skill_id: str) -> bool:
        return await self._repository.delete_personal_skill(tenant_id, user_id, skill_id)


def _display_name(skill: DraftSkill) -> str:
    """The model writes kebab-case names; the UI still needs a readable label."""
    label = skill.name.replace("-", " ").replace("_", " ").strip()
    return (label or skill.name)[:120].title()
