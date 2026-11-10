"""The lightweight Skill loop: evidence, explicit save/enable, and Run injection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from harness.assistant.models import (
    Objective,
    ObjectiveBrief,
    ObjectiveExecution,
    ObjectiveExecutionStatus,
    ObjectiveStatus,
    SpecialistStatus,
)
from harness.assistant.personal_skills import PersonalSkillService
from harness.assistant.service import AssistantService
from harness.assistant.skill_models import (
    PersonalSkill,
    SkillProposal,
    SkillProposalStatus,
)
from harness.assistant.skill_proposals import DISMISSAL_COOLDOWN, SkillProposalService
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import Run, RunStatus
from harness.studio.models import DraftSkill
from harness.studio.skill_builder import (
    SkillConversationMessage,
    SkillConversationReply,
    SkillConversationUpstreamError,
)

NOW = datetime(2026, 11, 10, tzinfo=UTC)


def make_execution(
    index: int,
    *,
    title: str = "整理每周销售周报",
    instructions: str = "读取本周销售数据，按地区汇总，生成周报并检查异常波动。",
    status: ObjectiveExecutionStatus = ObjectiveExecutionStatus.SUCCEEDED,
    trigger: str = "manual",
    objective_id: str | None = None,
) -> ObjectiveExecution:
    brief = ObjectiveBrief(
        title=title,
        instructions=instructions,
        agentName="assistant",
        agentVersion="1",
    )
    return ObjectiveExecution(
        executionId=f"e{index}",
        objectiveId=objective_id or f"o{index}",
        tenantId="t",
        userId="u",
        triggerKey=f"{trigger}:{index}",
        objectiveRevision=1,
        brief=brief,
        threadId=f"thread-{index}",
        runId=f"run-{index}",
        status=status,
        summary="已完成并检查。",
        createdAt=NOW + timedelta(minutes=index),
        finishedAt=NOW + timedelta(minutes=index, seconds=30),
    )


def make_objective(execution: ObjectiveExecution, *, specialist_id: str | None = None) -> Objective:
    return Objective(
        **execution.brief.model_dump(),
        objectiveId=execution.objective_id,
        tenantId=execution.tenant_id,
        userId=execution.user_id,
        status=ObjectiveStatus.ACTIVE,
        specialistId=specialist_id,
        createdAt=execution.created_at,
        updatedAt=execution.created_at,
    )


class FakeRepository:
    """In-memory equivalent of the CAS/unique constraints used by the services."""

    def __init__(self) -> None:
        self.executions: list[ObjectiveExecution] = []
        self.objectives: dict[str, Objective] = {}
        self.specialists: dict[str, Any] = {}
        self.proposals: dict[str, SkillProposal] = {}
        self.personal: dict[str, PersonalSkill] = {}

    async def recent_succeeded_executions(
        self, tenant_id: str, user_id: str, *, limit: int = 20
    ) -> list[ObjectiveExecution]:
        items = [
            item
            for item in self.executions
            if item.tenant_id == tenant_id
            and item.user_id == user_id
            and item.status is ObjectiveExecutionStatus.SUCCEEDED
            and item.run_id is not None
            and item.finished_at is not None
            and not item.skipped_no_model
            and "retry" not in item.trigger_key.lower()
        ]
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items[:limit]

    async def find_skill_proposal(self, tenant_id, user_id, fingerprint):
        for proposal in self.proposals.values():
            if (
                proposal.tenant_id == tenant_id
                and proposal.user_id == user_id
                and proposal.fingerprint == fingerprint
            ):
                return proposal
        return None

    async def list_skill_proposals(self, tenant_id, user_id, *, status=None, limit=50):
        items = [
            item
            for item in self.proposals.values()
            if item.tenant_id == tenant_id and item.user_id == user_id
        ]
        if status is not None:
            items = [item for item in items if item.status.value == status]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items[:limit]

    async def save_skill_proposal(self, proposal, *, expected_revision=None):
        current = self.proposals.get(proposal.proposal_id)
        if current is None:
            if expected_revision not in (None, 1):
                raise ConflictError("changed")
            if any(
                item.tenant_id == proposal.tenant_id
                and item.user_id == proposal.user_id
                and item.fingerprint == proposal.fingerprint
                for item in self.proposals.values()
            ):
                raise ConflictError("duplicate")
            self.proposals[proposal.proposal_id] = proposal
            return proposal
        if expected_revision is not None and current.revision != expected_revision:
            raise ConflictError("changed")
        self.proposals[proposal.proposal_id] = proposal
        return proposal

    async def skill_proposal(self, tenant_id, user_id, proposal_id):
        proposal = self.proposals.get(proposal_id)
        if (
            proposal is None
            or proposal.tenant_id != tenant_id
            or proposal.user_id != user_id
        ):
            raise NotFoundError("Skill proposal not found")
        return proposal

    async def get(self, tenant_id, user_id, objective_id):
        objective = self.objectives.get(objective_id)
        if (
            objective is None
            or objective.tenant_id != tenant_id
            or objective.user_id != user_id
        ):
            raise NotFoundError("Objective not found")
        return objective

    async def get_specialist(self, tenant_id, user_id, specialist_id):
        specialist = self.specialists.get(specialist_id)
        if specialist is None:
            raise NotFoundError("Specialist not found")
        return specialist

    async def find_personal_skill_by_name(self, tenant_id, user_id, name):
        for skill in self.personal.values():
            if (
                skill.tenant_id == tenant_id
                and skill.user_id == user_id
                and skill.name == name
            ):
                return skill
        return None

    async def create_personal_skill(self, skill):
        if await self.find_personal_skill_by_name(
            skill.tenant_id, skill.user_id, skill.name
        ):
            raise ConflictError("name exists")
        self.personal[skill.skill_id] = skill
        return skill

    async def personal_skill(self, tenant_id, user_id, skill_id):
        skill = self.personal.get(skill_id)
        if skill is None or skill.tenant_id != tenant_id or skill.user_id != user_id:
            raise NotFoundError("Personal Skill not found")
        return skill

    async def list_personal_skills(self, tenant_id, user_id, *, enabled_only=False):
        items = [
            item
            for item in self.personal.values()
            if item.tenant_id == tenant_id
            and item.user_id == user_id
            and (item.enabled or not enabled_only)
        ]
        return items

    async def set_personal_skill_enabled(
        self, tenant_id, user_id, skill_id, *, enabled, expected_revision, now
    ):
        current = await self.personal_skill(tenant_id, user_id, skill_id)
        if current.revision != expected_revision:
            raise ConflictError("changed")
        updated = current.model_copy(
            update={"enabled": enabled, "revision": current.revision + 1, "updated_at": now}
        )
        self.personal[skill_id] = updated
        return updated

    async def delete_personal_skill(self, tenant_id, user_id, skill_id):
        if skill_id not in self.personal:
            return False
        del self.personal[skill_id]
        return True


class FakeEvents:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_after(self, tenant_id, run_id, after_sequence, *, types=None):
        self.calls.append(run_id)
        return [SimpleNamespace(payload={"name": "Bash"})]


class FakeProjects:
    async def get(self, *, tenant_id, user_id, project_id):
        if project_id == "gone":
            raise NotFoundError("Project not found")
        return SimpleNamespace(project_id=project_id)


class FakeCatalogs:
    async def get_for_user(self, tenant_id, user_id):
        return SimpleNamespace(
            catalog=SimpleNamespace(
                model_routes=[
                    SimpleNamespace(route_id="route-1", enabled=True, model_type="chat")
                ]
            )
        )


class FakeConversation:
    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.requests: list[Any] = []

    async def respond(self, tenant_id, request):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


DRAFT = DraftSkill(
    name="weekly-sales-report",
    description="汇总每周销售数据并生成带异常检查的周报",
    instructions="1. 读取销售数据。\n2. 按地区汇总。\n3. 检查异常波动并输出周报。",
)


def services(repository: FakeRepository, conversation: FakeConversation | None = None):
    personal = PersonalSkillService(repository)
    proposals = SkillProposalService(
        repository,
        events=FakeEvents(),
        personal_skills=personal,
        projects=FakeProjects(),
        skill_conversation=conversation,
        catalogs=FakeCatalogs(),
    )
    return personal, proposals


def seed_previous_success(repository: FakeRepository, index: int = 1):
    execution = make_execution(index)
    repository.executions.append(execution)
    repository.objectives[execution.objective_id] = make_objective(execution)
    return execution


@pytest.mark.asyncio
async def test_one_success_is_not_a_pattern_and_two_independent_ones_are():
    repository = FakeRepository()
    _, proposals = services(repository)
    first = seed_previous_success(repository)

    assert await proposals.observe_success(first) is None

    second = make_execution(2)
    repository.executions.append(second)
    repository.objectives[second.objective_id] = make_objective(second)
    proposal = await proposals.observe_success(second)

    assert proposal is not None
    assert proposal.status is SkillProposalStatus.PROPOSED
    assert len(proposal.sources) == 2
    assert {source.objective_id for source in proposal.sources} == {"o1", "o2"}
    assert proposal.sources[0].tools == ("Bash",)


@pytest.mark.asyncio
async def test_failed_cancelled_retry_and_same_objective_do_not_count():
    repository = FakeRepository()
    _, proposals = services(repository)
    seed_previous_success(repository)

    failed = make_execution(2, status=ObjectiveExecutionStatus.FAILED)
    cancelled = make_execution(3, status=ObjectiveExecutionStatus.CANCELLED)
    retry = make_execution(4, trigger="retry")
    same_objective = make_execution(5, objective_id="o1")
    for execution in (failed, cancelled, retry, same_objective):
        repository.executions.append(execution)
        repository.objectives.setdefault(
            execution.objective_id, make_objective(execution)
        )
        assert await proposals.observe_success(execution) is None

    assert repository.proposals == {}


@pytest.mark.asyncio
async def test_dismissal_cools_down_then_allows_a_new_proposal():
    repository = FakeRepository()
    _, proposals = services(repository)
    seed_previous_success(repository)
    second = make_execution(2)
    repository.executions.append(second)
    repository.objectives[second.objective_id] = make_objective(second)
    proposal = await proposals.observe_success(second)
    assert proposal is not None

    dismissed = await proposals.dismiss(
        "t", "u", proposal.proposal_id, expected_revision=proposal.revision
    )
    assert dismissed.status is SkillProposalStatus.DISMISSED
    assert dismissed.cooldown_until is not None
    assert dismissed.cooldown_until > datetime.now(UTC) + DISMISSAL_COOLDOWN - timedelta(
        minutes=1
    )

    third = make_execution(3)
    repository.executions.append(third)
    repository.objectives[third.objective_id] = make_objective(third)
    cooled = await proposals.observe_success(third)
    assert cooled is not None
    assert cooled.revision == dismissed.revision

    repository.proposals[dismissed.proposal_id] = dismissed.model_copy(
        update={"cooldown_until": NOW - timedelta(seconds=1)}
    )
    resumed = await proposals.observe_success(third)
    assert resumed is not None
    assert resumed.status is SkillProposalStatus.PROPOSED
    assert resumed.cooldown_until is None


@pytest.mark.asyncio
async def test_generation_failure_keeps_proposal_retryable_then_drafts():
    repository = FakeRepository()
    seed_previous_success(repository)
    second = make_execution(2)
    repository.executions.append(second)
    repository.objectives[second.objective_id] = make_objective(second)
    conversation = FakeConversation(
        SkillConversationUpstreamError("gateway down"),
        SkillConversationReply(
            status="ready",
            reply="已生成草稿",
            skill=DRAFT,
        ),
    )
    _, proposals = services(repository, conversation)
    proposal = await proposals.observe_success(second)
    assert proposal is not None

    with pytest.raises(ConflictError):
        await proposals.generate("t", "u", proposal.proposal_id)
    assert repository.proposals[proposal.proposal_id].status is SkillProposalStatus.PROPOSED

    generated = await proposals.generate("t", "u", proposal.proposal_id)
    assert generated.status is SkillProposalStatus.DRAFT
    assert generated.draft == DRAFT
    assert len(conversation.requests) == 2
    assert isinstance(conversation.requests[0].messages[0], SkillConversationMessage)


@pytest.mark.asyncio
async def test_save_is_idempotent_disabled_and_enable_is_explicit():
    repository = FakeRepository()
    seed_previous_success(repository)
    second = make_execution(2)
    repository.executions.append(second)
    repository.objectives[second.objective_id] = make_objective(second)
    conversation = FakeConversation(
        SkillConversationReply(status="ready", reply="已生成草稿", skill=DRAFT)
    )
    personal, proposals = services(repository, conversation)
    proposal = await proposals.observe_success(second)
    assert proposal is not None
    draft = await proposals.generate("t", "u", proposal.proposal_id)

    saved = await proposals.save(
        "t", "u", proposal.proposal_id, expected_revision=draft.revision
    )
    again = await proposals.save(
        "t", "u", proposal.proposal_id, expected_revision=draft.revision
    )
    assert saved.skill_id == again.skill_id
    assert saved.enabled is False
    assert len(repository.personal) == 1
    assert await personal.enabled_snapshots("t", "u") == ()

    enabled = await personal.set_enabled(
        "t", "u", saved.skill_id, enabled=True, expected_revision=saved.revision
    )
    assert enabled.enabled is True
    snapshots = await personal.enabled_snapshots("t", "u")
    assert [snapshot.source for snapshot in snapshots] == [
        f"personal-skill:{saved.skill_id}@{enabled.revision}"
    ]


@pytest.mark.asyncio
async def test_other_user_cannot_read_or_generate_from_the_proposal():
    repository = FakeRepository()
    seed_previous_success(repository)
    second = make_execution(2)
    repository.executions.append(second)
    repository.objectives[second.objective_id] = make_objective(second)
    conversation = FakeConversation(
        SkillConversationReply(status="ready", reply="已生成草稿", skill=DRAFT)
    )
    _, proposals = services(repository, conversation)
    proposal = await proposals.observe_success(second)
    assert proposal is not None

    with pytest.raises(NotFoundError):
        await proposals.generate("t", "someone-else", proposal.proposal_id)
    with pytest.raises(NotFoundError):
        await proposals.dismiss(
            "t", "someone-else", proposal.proposal_id, expected_revision=1
        )


@pytest.mark.asyncio
async def test_deleted_project_or_archived_specialist_blocks_generation():
    repository = FakeRepository()
    first = make_execution(
        1,
        instructions="读取项目销售数据，按地区汇总，生成周报并检查异常。",
    )
    first = first.model_copy(
        update={"brief": first.brief.model_copy(update={"project_id": "gone"})}
    )
    repository.executions.append(first)
    repository.objectives[first.objective_id] = make_objective(first)
    second = make_execution(2)
    repository.executions.append(second)
    repository.objectives[second.objective_id] = make_objective(second)
    conversation = FakeConversation(
        SkillConversationReply(status="ready", reply="已生成草稿", skill=DRAFT)
    )
    _, proposals = services(repository, conversation)
    proposal = await proposals.observe_success(second)
    assert proposal is not None

    with pytest.raises(NotFoundError):
        await proposals.generate("t", "u", proposal.proposal_id)

    repository.objectives[first.objective_id] = make_objective(
        first, specialist_id="sp-1"
    ).model_copy(update={"project_id": None})
    repository.specialists["sp-1"] = SimpleNamespace(
        status=SpecialistStatus.ARCHIVED
    )
    with pytest.raises(ConflictError):
        await proposals.generate("t", "u", proposal.proposal_id)


@pytest.mark.asyncio
async def test_enabled_personal_skill_is_passed_to_personal_assistant_runs():
    snapshot = SimpleNamespace(source="personal-skill:pskill_1@2")
    personal = AsyncMock()
    personal.enabled_snapshots.return_value = (snapshot,)
    brief = ObjectiveBrief(
        title="整理周报",
        instructions="读取数据并汇总。",
        agentName="assistant",
        agentVersion="1",
    )
    objective = Objective(
        **brief.model_dump(),
        objectiveId="o1",
        tenantId="t",
        userId="u",
        status=ObjectiveStatus.ACTIVE,
        createdAt=NOW,
        updatedAt=NOW,
    )
    execution = ObjectiveExecution(
        executionId="e1",
        objectiveId="o1",
        tenantId="t",
        userId="u",
        triggerKey="manual:1",
        objectiveRevision=1,
        brief=brief,
        threadId="thread",
        createdAt=NOW,
    )
    run = Run(
        run_id="run1",
        session_id="session",
        tenant_id="t",
        status=RunStatus.RUNNING,
        idempotency_key="e1",
        created_at=NOW,
        updated_at=NOW,
    )
    repo, agents, auth, projects, agui, runs, events = [AsyncMock() for _ in range(7)]
    repo.get.return_value = objective
    auth.profile.return_value = (
        SimpleNamespace(disabled=False),
        SimpleNamespace(role="member"),
    )
    runs.find_by_idempotency_key.return_value = None
    agui.create_run_with_result.return_value = SimpleNamespace(run=run)
    service = AssistantService(
        repo,
        agents=agents,
        auth=auth,
        projects=projects,
        agui=agui,
        runs=runs,
        events=events,
        personal_skills=personal,
    )
    service._skip_unchanged_check = AsyncMock(return_value=False)
    service.managed_overview = AsyncMock(return_value=[])

    result = await service._reconcile(execution)

    assert result.run_id == "run1"
    personal.enabled_snapshots.assert_awaited_once_with("t", "u")
    assert agui.create_run_with_result.await_args.kwargs["personal_skill_snapshots"] == (
        snapshot,
    )
