"""Detect repeated completed work and turn it into an explicit Skill proposal.

Detection is deliberately conservative: two distinct successful objectives must
share a recognisable intent, retries and model-less checks never count, and a
dismissal suppresses the same pattern for seven days. Nothing here enables a
Skill; the user must still generate, save, and enable it.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from harness.assistant.models import (
    ObjectiveExecution,
    ObjectiveExecutionStatus,
    SpecialistStatus,
)
from harness.assistant.personal_skills import PersonalSkillService
from harness.assistant.repository import PostgresAssistantRepository
from harness.assistant.skill_models import (
    PersonalSkill,
    SkillProposal,
    SkillProposalSource,
    SkillProposalStatus,
)
from harness.core.errors import ConflictError
from harness.core.ports import EventRepository
from harness.studio.skill_builder import (
    SkillConversationContext,
    SkillConversationMessage,
    SkillConversationRequest,
    SkillConversationUnavailableError,
    SkillConversationUpstreamError,
)
from harness.studio.skill_import import SkillImportError, validate_authored_skill

SIMILARITY_THRESHOLD = 0.5
DISMISSAL_COOLDOWN = timedelta(days=7)
MAX_SOURCES = 8
MAX_EVIDENCE = 5

_WORD = re.compile(r"[a-z][a-z0-9_-]+")
_CJK_RUN = re.compile(r"[一-鿿]+")
_STOP_WORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "please",
    "task", "work", "check", "update", "report", "using", "about", "then",
}
_STOP_BIGRAMS = {
    "我们", "你们", "这个", "那个", "一个", "帮我", "请帮", "一下", "可以",
    "需要", "进行", "以及", "然后", "如果", "已经", "现在", "今天", "明天",
    "本次", "任务", "工作", "完成", "相关",
}


def _keywords(text: str) -> frozenset[str]:
    """Normalise dates/numbers away and keep comparable Latin words and CJK bigrams."""
    lowered = text.lower()
    lowered = re.sub(r"\d+", " ", lowered)
    words = {
        word
        for word in _WORD.findall(lowered)
        if len(word) >= 3 and word not in _STOP_WORDS
    }
    grams: set[str] = set()
    for run in _CJK_RUN.findall(lowered):
        for index in range(len(run) - 1):
            gram = run[index : index + 2]
            if gram not in _STOP_BIGRAMS:
                grams.add(gram)
    return frozenset(words | grams)


def _similarity(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _fingerprint(keywords: Iterable[str]) -> str:
    return hashlib.sha256("|".join(sorted(keywords)[:16]).encode()).hexdigest()


def _text(execution: ObjectiveExecution) -> str:
    return f"{execution.brief.title}\n{execution.brief.instructions}"


class SkillProposalService:
    def __init__(
        self,
        repository: PostgresAssistantRepository,
        *,
        events: EventRepository,
        personal_skills: PersonalSkillService,
        projects: Any = None,
        skill_conversation: Any = None,
        catalogs: Any = None,
    ) -> None:
        self._repository = repository
        self._events = events
        self._personal_skills = personal_skills
        self._projects = projects
        self._skill_conversation = skill_conversation
        self._catalogs = catalogs

    def with_repository(self, repository: PostgresAssistantRepository) -> SkillProposalService:
        return SkillProposalService(
            repository,
            events=self._events,
            personal_skills=self._personal_skills.with_repository(repository),
            projects=self._projects,
            skill_conversation=self._skill_conversation,
            catalogs=self._catalogs,
        )

    # -- collection ------------------------------------------------------------

    async def list(
        self, tenant_id: str, user_id: str, *, status: str | None = None
    ) -> list[SkillProposal]:
        return await self._repository.list_skill_proposals(
            tenant_id, user_id, status=status
        )

    async def observe_success(
        self, execution: ObjectiveExecution
    ) -> SkillProposal | None:
        if (
            execution.status is not ObjectiveExecutionStatus.SUCCEEDED
            or execution.run_id is None
            or execution.finished_at is None
            or execution.skipped_no_model
            or "retry" in execution.trigger_key.lower()
        ):
            return None
        signature = _keywords(_text(execution))
        if len(signature) < 3:
            return None
        recent = await self._repository.recent_succeeded_executions(
            execution.tenant_id, execution.user_id, limit=20
        )
        peers = [
            item
            for item in recent
            if item.execution_id != execution.execution_id
            and item.objective_id != execution.objective_id
            and item.run_id is not None
            and _similarity(signature, _keywords(_text(item))) >= SIMILARITY_THRESHOLD
        ]
        if not peers:
            # One success is not a pattern; require a real second objective.
            return None
        evidence = sorted(
            (execution, *peers[: MAX_EVIDENCE - 1]),
            key=lambda item: item.finished_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        common: set[str] = set()
        for peer in evidence[1:]:
            common.update(signature & _keywords(_text(peer)))
        if not common:
            return None
        keywords = tuple(sorted(common)[:16])
        fingerprint = _fingerprint(keywords)
        now = datetime.now(UTC)
        existing = await self._repository.find_skill_proposal(
            execution.tenant_id, execution.user_id, fingerprint
        )
        if existing is None:
            existing = await self._match_existing(
                execution.tenant_id, execution.user_id, signature
            )
        tools = await self._tool_names(execution.tenant_id, execution.run_id)
        sources = self._sources(evidence, tools)
        if existing is not None:
            if existing.status is SkillProposalStatus.SAVED:
                return existing
            if (
                existing.status is SkillProposalStatus.DISMISSED
                and existing.cooldown_until is not None
                and existing.cooldown_until > now
            ):
                return existing
            merged = self._merge_sources(existing.sources, sources)
            updated = existing.model_copy(
                update={
                    "status": SkillProposalStatus.PROPOSED,
                    "title": execution.brief.title[:120],
                    "summary": _summary(merged),
                    "keywords": keywords,
                    "sources": merged,
                    "cooldown_until": None,
                    "revision": existing.revision + 1,
                    "updated_at": now,
                }
            )
            try:
                return await self._repository.save_skill_proposal(
                    updated, expected_revision=existing.revision
                )
            except ConflictError:
                return await self._repository.skill_proposal(
                    execution.tenant_id, execution.user_id, existing.proposal_id
                )
        proposal = SkillProposal(
            proposalId=f"skillproposal_{uuid4().hex}",
            tenantId=execution.tenant_id,
            userId=execution.user_id,
            fingerprint=fingerprint,
            status=SkillProposalStatus.PROPOSED,
            title=execution.brief.title[:120],
            summary=_summary(sources),
            keywords=keywords,
            sources=sources,
            createdAt=now,
            updatedAt=now,
        )
        try:
            return await self._repository.save_skill_proposal(proposal)
        except ConflictError:
            existing = await self._repository.find_skill_proposal(
                execution.tenant_id, execution.user_id, fingerprint
            )
            return existing

    async def _match_existing(
        self, tenant_id: str, user_id: str, signature: frozenset[str]
    ) -> SkillProposal | None:
        proposals = await self._repository.list_skill_proposals(
            tenant_id, user_id, limit=50
        )
        scored = [
            (
                _similarity(signature, frozenset(proposal.keywords)),
                proposal,
            )
            for proposal in proposals
            if proposal.status is not SkillProposalStatus.SAVED
        ]
        scored = [item for item in scored if item[0] >= SIMILARITY_THRESHOLD]
        return max(scored, key=lambda item: item[0])[1] if scored else None

    async def _tool_names(self, tenant_id: str, run_id: str) -> tuple[str, ...]:
        try:
            events = await self._events.list_after(
                tenant_id, run_id, 0, types=("tool.request",)
            )
        except Exception:
            return ()
        names: list[str] = []
        for event in events:
            name = str(event.payload.get("name") or event.payload.get("toolName") or "")
            if name and name not in names:
                names.append(name)
        return tuple(names[:20])

    @staticmethod
    def _sources(
        executions: Iterable[ObjectiveExecution], tools: tuple[str, ...]
    ) -> tuple[SkillProposalSource, ...]:
        sources: list[SkillProposalSource] = []
        seen: set[str] = set()
        for execution in executions:
            if execution.execution_id in seen or execution.run_id is None:
                continue
            seen.add(execution.execution_id)
            sources.append(
                SkillProposalSource(
                    objectiveId=execution.objective_id,
                    executionId=execution.execution_id,
                    runId=execution.run_id,
                    threadId=execution.thread_id,
                    title=execution.brief.title[:120],
                    summary=(execution.summary or "")[:2_000],
                    projectId=execution.brief.project_id,
                    tools=tools if not sources else (),
                    finishedAt=execution.finished_at,
                )
            )
        return tuple(sources[:MAX_SOURCES])

    @staticmethod
    def _merge_sources(
        current: tuple[SkillProposalSource, ...],
        incoming: tuple[SkillProposalSource, ...],
    ) -> tuple[SkillProposalSource, ...]:
        merged: list[SkillProposalSource] = []
        seen: set[str] = set()
        for source in (*incoming, *current):
            if source.execution_id in seen:
                continue
            seen.add(source.execution_id)
            merged.append(source)
        return tuple(merged[:MAX_SOURCES])

    # -- user actions ----------------------------------------------------------

    async def generate(
        self,
        tenant_id: str,
        user_id: str,
        proposal_id: str,
        *,
        model_route: str | None = None,
    ) -> SkillProposal:
        proposal = await self._repository.skill_proposal(
            tenant_id, user_id, proposal_id
        )
        if proposal.status is SkillProposalStatus.SAVED:
            return proposal
        now = datetime.now(UTC)
        if (
            proposal.status is SkillProposalStatus.DISMISSED
            and proposal.cooldown_until is not None
            and proposal.cooldown_until > now
        ):
            raise ConflictError("该建议已忽略，冷却期内不再生成")
        if self._skill_conversation is None:
            raise ConflictError("技能生成服务未配置")
        transcript = await self._recheck_sources(proposal)
        route = model_route
        if not route and self._catalogs is not None:
            catalog = await self._catalogs.get_for_user(tenant_id, user_id)
            route = next(
                (
                    item.route_id
                    for item in catalog.catalog.model_routes
                    if item.enabled and item.model_type in {"chat", "vision"}
                ),
                None,
            )
        if not route:
            raise ConflictError("请先配置可用的对话模型")
        request = SkillConversationRequest(
            modelRoute=route,
            context=SkillConversationContext(
                agentName="personal-assistant",
                displayName="个人助理",
                domain="personal-workflow",
                description=(proposal.summary or proposal.title)[:500],
                currentSkill=(
                    proposal.draft
                    if proposal.status is SkillProposalStatus.DRAFT
                    else None
                ),
            ),
            messages=(
                SkillConversationMessage(
                    role="user",
                    content=_authoring_prompt(proposal, transcript),
                ),
            ),
        )
        try:
            reply = await self._skill_conversation.respond(tenant_id, request)
        except SkillConversationUnavailableError as error:
            raise ConflictError(str(error)) from error
        except SkillConversationUpstreamError as error:
            raise ConflictError(f"技能生成暂时失败，可稍后重试：{error}") from error
        changes: dict[str, object] = {
            "reply": reply.reply,
            "follow_up_questions": reply.follow_up_questions,
            "error": None,
            "revision": proposal.revision + 1,
            "updated_at": datetime.now(UTC),
        }
        if reply.status == "ready" and reply.skill is not None:
            try:
                validate_authored_skill(reply.skill)
            except SkillImportError as error:
                raise ConflictError(str(error)) from error
            changes.update(
                status=SkillProposalStatus.DRAFT,
                draft=reply.skill,
                follow_up_questions=(),
            )
        else:
            changes.update(status=SkillProposalStatus.PROPOSED)
        updated = proposal.model_copy(update=changes)
        return await self._repository.save_skill_proposal(
            updated, expected_revision=proposal.revision
        )

    async def save(
        self,
        tenant_id: str,
        user_id: str,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> PersonalSkill:
        proposal = await self._repository.skill_proposal(
            tenant_id, user_id, proposal_id
        )
        if proposal.status is SkillProposalStatus.SAVED and proposal.skill_id:
            return await self._personal_skills.get(tenant_id, user_id, proposal.skill_id)
        if proposal.status is not SkillProposalStatus.DRAFT or proposal.draft is None:
            raise ConflictError("请先生成 Skill 草稿")
        if proposal.revision != expected_revision:
            raise ConflictError("Skill 建议已变化，请刷新后确认")
        await self._recheck_sources(proposal)
        try:
            skill = await self._personal_skills.save_draft(
                tenant_id,
                user_id,
                skill=proposal.draft,
                source_proposal=proposal,
            )
        except (ValueError, SkillImportError) as error:
            raise ConflictError(str(error)) from error
        except ConflictError:
            # A concurrent confirmation created the same disabled Skill first.
            existing = await self._repository.find_personal_skill_by_name(
                tenant_id, user_id, proposal.draft.name
            )
            if existing is None or existing.source_proposal_id != proposal.proposal_id:
                raise
            skill = existing
        updated = proposal.model_copy(
            update={
                "status": SkillProposalStatus.SAVED,
                "skill_id": skill.skill_id,
                "revision": proposal.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            await self._repository.save_skill_proposal(
                updated, expected_revision=proposal.revision
            )
        except ConflictError:
            # A concurrent confirmation may have won; its saved Skill is the truth.
            current = await self._repository.skill_proposal(
                tenant_id, user_id, proposal_id
            )
            if current.status is SkillProposalStatus.SAVED and current.skill_id:
                return await self._personal_skills.get(
                    tenant_id, user_id, current.skill_id
                )
            raise
        return skill

    async def dismiss(
        self,
        tenant_id: str,
        user_id: str,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> SkillProposal:
        proposal = await self._repository.skill_proposal(
            tenant_id, user_id, proposal_id
        )
        if proposal.status is SkillProposalStatus.SAVED:
            raise ConflictError("已保存的 Skill 不能在建议中忽略")
        now = datetime.now(UTC)
        if (
            proposal.status is SkillProposalStatus.DISMISSED
            and proposal.cooldown_until is not None
            and proposal.cooldown_until > now
        ):
            return proposal
        if proposal.revision != expected_revision:
            raise ConflictError("Skill 建议已变化，请刷新后忽略")
        updated = proposal.model_copy(
            update={
                "status": SkillProposalStatus.DISMISSED,
                "cooldown_until": now + DISMISSAL_COOLDOWN,
                "revision": proposal.revision + 1,
                "updated_at": now,
            }
        )
        return await self._repository.save_skill_proposal(
            updated, expected_revision=proposal.revision
        )

    async def _recheck_sources(self, proposal: SkillProposal) -> str:
        """Re-authorise every cited objective at generation time, not just collection."""
        chunks: list[str] = []
        seen: set[str] = set()
        for source in proposal.sources:
            if source.objective_id in seen:
                continue
            seen.add(source.objective_id)
            objective = await self._repository.get(
                proposal.tenant_id, proposal.user_id, source.objective_id
            )
            if objective.project_id and self._projects is not None:
                await self._projects.get(
                    tenant_id=proposal.tenant_id,
                    user_id=proposal.user_id,
                    project_id=objective.project_id,
                )
            if objective.specialist_id:
                specialist = await self._repository.get_specialist(
                    proposal.tenant_id, proposal.user_id, objective.specialist_id
                )
                if specialist.status is SpecialistStatus.ARCHIVED:
                    raise ConflictError("来源助理已归档，无法生成 Skill")
            chunks.append(
                f"任务：{objective.title}\n要求：{objective.instructions}\n"
                f"结果：{(source.summary or '已完成')[:800]}"
            )
        if not chunks:
            raise ConflictError("来源任务不可用，无法生成 Skill")
        return "\n\n".join(chunks)


def _summary(sources: tuple[SkillProposalSource, ...]) -> str:
    titles = list(dict.fromkeys(source.title for source in sources if source.title))[:4]
    return (
        f"最近 {len(sources)} 个独立任务使用了相似流程，可以考虑固化为个人 Skill。"
        + ("示例：" + "、".join(titles) if titles else "")
    )[:2_000]


def _authoring_prompt(proposal: SkillProposal, transcript: str) -> str:
    goal = (
        "以下是一名用户最近独立完成的同类任务记录。请判断其中是否形成了值得复用的"
        "个人工作方法；若有，提炼成个人 Skill 草稿。不要执行原业务任务，不要修改智能体。"
        "把企业、客户、时间等本次数据参数化，不包含凭据；只保留有依据的步骤、"
        "输入输出契约和核验要求。信息足够时直接返回 ready 草稿。"
    )
    return f"{goal}\n\n建议标题：{proposal.title}\n\n{transcript}"[:12_000]
