"""Contracts for turning repeated completed work into an explicit personal Skill.

A proposal is evidence, not permission: it records which independent executions
looked alike, but the user still has to generate a draft, save it, and enable
it before any Run uses it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field

from harness.studio.models import DraftSkill, StudioModel


class SkillProposalStatus(StrEnum):
    PROPOSED = "proposed"
    DRAFT = "draft"
    SAVED = "saved"
    DISMISSED = "dismissed"


class SkillProposalSource(StudioModel):
    """One completed execution cited as evidence for a proposal."""

    objective_id: str = Field(alias="objectiveId")
    execution_id: str = Field(alias="executionId")
    run_id: str = Field(alias="runId")
    thread_id: str = Field(alias="threadId")
    title: str = Field(default="", max_length=120)
    summary: str = Field(default="", max_length=2_000)
    project_id: str | None = Field(default=None, alias="projectId", max_length=128)
    specialist_id: str | None = Field(default=None, alias="specialistId", max_length=128)
    tools: tuple[str, ...] = Field(default=(), max_length=40)
    finished_at: AwareDatetime | None = Field(default=None, alias="finishedAt")


class SkillProposal(StudioModel):
    proposal_id: str = Field(alias="proposalId")
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: SkillProposalStatus = SkillProposalStatus.PROPOSED
    revision: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=2_000)
    # Normalised keyword set used to recognise the same intent later. It stays
    # server-side; the UI only needs the human title and summary.
    keywords: tuple[str, ...] = Field(default=(), max_length=40)
    sources: tuple[SkillProposalSource, ...] = Field(default=(), max_length=20)
    draft: DraftSkill | None = None
    reply: str = Field(default="", max_length=4_000)
    follow_up_questions: tuple[str, ...] = Field(
        default=(), alias="followUpQuestions", max_length=3
    )
    skill_id: str | None = Field(default=None, alias="skillId")
    error: str | None = Field(default=None, max_length=2_000)
    cooldown_until: AwareDatetime | None = Field(default=None, alias="cooldownUntil")
    created_at: AwareDatetime = Field(alias="createdAt")
    updated_at: AwareDatetime = Field(alias="updatedAt")

    @property
    def source_objective_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(source.objective_id for source in self.sources))


class PersonalSkill(StudioModel):
    """A user-owned Skill. Saving never enables it; enabling is a separate act."""

    skill_id: str = Field(alias="skillId")
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    display_name: str = Field(alias="displayName", min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    enabled: bool = False
    revision: int = Field(default=1, ge=1)
    source_proposal_id: str | None = Field(
        default=None, alias="sourceProposalId", max_length=128
    )
    skill: DraftSkill
    created_at: AwareDatetime = Field(alias="createdAt")
    updated_at: AwareDatetime = Field(alias="updatedAt")
