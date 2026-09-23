"""Tenant-and-owner-scoped Agent Draft persistence ports and adapters."""

from __future__ import annotations

import asyncio
from typing import Protocol

from harness.core.errors import ConflictError, NotFoundError
from harness.studio.models import AgentDraft, AgentDraftSummary, DraftRevisionSummary


class AgentDraftRepository(Protocol):
    async def add_child(
        self, expected_revision: int, parent: AgentDraft, child: AgentDraft
    ) -> None: ...

    async def add(self, draft: AgentDraft) -> None: ...

    async def get(self, tenant_id: str, owner_user_id: str, draft_id: str) -> AgentDraft: ...

    async def list_revisions(
        self, tenant_id: str, owner_user_id: str, draft_id: str,
        *, before_revision: int | None = None, limit: int = 50,
    ) -> list[DraftRevisionSummary]: ...

    async def get_revision(
        self, tenant_id: str, owner_user_id: str, draft_id: str, revision: int,
    ) -> AgentDraft: ...

    async def list_for_user(self, tenant_id: str, owner_user_id: str) -> list[AgentDraft]: ...

    async def list_summaries(
        self, tenant_id: str, owner_user_id: str
    ) -> list[AgentDraftSummary]: ...

    async def list_all_for_tenant(self, tenant_id: str) -> list[AgentDraft]: ...

    async def replace(self, expected_revision: int, draft: AgentDraft) -> None: ...

    async def delete(
        self,
        tenant_id: str,
        owner_user_id: str,
        draft_id: str,
        expected_revision: int,
    ) -> None: ...

    async def get_by_agent(self, tenant_id: str, agent_id: str) -> AgentDraft | None:
        """The shared draft of a workspace Agent, if one exists."""
        ...

    async def get_shared(self, tenant_id: str, draft_id: str) -> AgentDraft | None:
        """A space-bound draft resolved across creators by draft_id."""
        ...

    async def move_owner(
        self, tenant_id: str, from_user_id: str, to_user_id: str, name: str
    ) -> int:
        """Re-key drafts of one personal Agent to a new owner."""
        ...


class InMemoryAgentDraftRepository:
    """Optimistic tenant-and-owner-scoped storage used by tests and previews."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], AgentDraft] = {}
        self._history: dict[tuple[str, str, str, int], AgentDraft] = {}
        self._lock = asyncio.Lock()

    async def add(self, draft: AgentDraft) -> None:
        key = (draft.tenant_id, draft.created_by, draft.draft_id)
        async with self._lock:
            if key in self._items:
                raise ConflictError(f"Agent draft already exists: {draft.draft_id}")
            self._items[key] = draft

    async def get(self, tenant_id: str, owner_user_id: str, draft_id: str) -> AgentDraft:
        try:
            return self._items[(tenant_id, owner_user_id, draft_id)]
        except KeyError as error:
            raise NotFoundError(f"Agent draft not found: {draft_id}") from error

    async def add_child(
        self, expected_revision: int, parent: AgentDraft, child: AgentDraft
    ) -> None:
        parent_key = (parent.tenant_id, parent.created_by, parent.draft_id)
        child_key = (child.tenant_id, child.created_by, child.draft_id)
        async with self._lock:
            current = self._items.get(parent_key)
            if current is None or current.revision != expected_revision:
                raise ConflictError("父智能体已更新，请刷新后重试")
            if parent.revision != expected_revision + 1 or child_key in self._items:
                raise ConflictError("子智能体创建冲突，请刷新后重试")
            self._history[(*parent_key, current.revision)] = current
            self._items[parent_key] = parent
            self._items[child_key] = child

    async def list_revisions(
        self, tenant_id: str, owner_user_id: str, draft_id: str,
        *, before_revision: int | None = None, limit: int = 50,
    ) -> list[DraftRevisionSummary]:
        current = await self.get(tenant_id, owner_user_id, draft_id)
        snapshots = [v for k, v in self._history.items()
                     if k[:3] == (tenant_id, owner_user_id, draft_id)] + [current]
        return [DraftRevisionSummary(revision=d.revision, updatedAt=d.updated_at)
                for d in sorted(snapshots, key=lambda d: d.revision, reverse=True)
                if before_revision is None or d.revision < before_revision][:limit]

    async def get_revision(
        self, tenant_id: str, owner_user_id: str, draft_id: str, revision: int,
    ) -> AgentDraft:
        current = await self.get(tenant_id, owner_user_id, draft_id)
        if current.revision == revision:
            return current
        result = self._history.get((tenant_id, owner_user_id, draft_id, revision))
        if result is None:
            raise NotFoundError("草稿修订未留存")
        return result

    async def list_for_user(self, tenant_id: str, owner_user_id: str) -> list[AgentDraft]:
        return sorted(
            (
                draft
                for (stored_tenant, stored_owner, _draft_id), draft in self._items.items()
                if stored_tenant == tenant_id and stored_owner == owner_user_id
            ),
            key=lambda draft: (draft.updated_at, draft.draft_id),
            reverse=True,
        )

    async def list_summaries(self, tenant_id: str, owner_user_id: str) -> list[AgentDraftSummary]:
        return [
            AgentDraftSummary.from_draft(draft)
            for draft in await self.list_for_user(tenant_id, owner_user_id)
        ]

    async def list_all_for_tenant(self, tenant_id: str) -> list[AgentDraft]:
        return sorted(
            (
                draft
                for (stored_tenant, _owner, _draft_id), draft in self._items.items()
                if stored_tenant == tenant_id
            ),
            key=lambda draft: (draft.updated_at, draft.draft_id),
            reverse=True,
        )

    async def replace(self, expected_revision: int, draft: AgentDraft) -> None:
        key = (draft.tenant_id, draft.created_by, draft.draft_id)
        async with self._lock:
            current = self._items.get(key)
            if current is None:
                raise NotFoundError(f"Agent draft not found: {draft.draft_id}")
            if current.revision != expected_revision:
                raise ConflictError(
                    "Agent draft revision changed: "
                    f"expected={expected_revision} actual={current.revision}"
                )
            if draft.revision != expected_revision + 1:
                raise ConflictError("Agent draft replacement must increment revision once")
            self._history[(*key, current.revision)] = current
            self._items[key] = draft

    async def delete(
        self,
        tenant_id: str,
        owner_user_id: str,
        draft_id: str,
        expected_revision: int,
    ) -> None:
        key = (tenant_id, owner_user_id, draft_id)
        async with self._lock:
            current = self._items.get(key)
            if current is None:
                raise NotFoundError(f"Agent draft not found: {draft_id}")
            if current.revision != expected_revision:
                raise ConflictError(
                    "Agent draft revision changed: "
                    f"expected={expected_revision} actual={current.revision}"
                )
            del self._items[key]
            self._history = {k: v for k, v in self._history.items() if k[:3] != key}

    async def get_by_agent(self, tenant_id: str, agent_id: str) -> AgentDraft | None:
        for draft in self._items.values():
            if draft.tenant_id == tenant_id and draft.agent_id == agent_id:
                return draft
        return None

    async def get_shared(self, tenant_id: str, draft_id: str) -> AgentDraft | None:
        for draft in self._items.values():
            if (
                draft.tenant_id == tenant_id
                and draft.draft_id == draft_id
                and draft.space_id is not None
            ):
                return draft
        return None

    async def move_owner(
        self, tenant_id: str, from_user_id: str, to_user_id: str, name: str
    ) -> int:
        if from_user_id == to_user_id:
            return 0
        moved_keys = [
            key
            for key in self._items
            if key[0] == tenant_id and key[1] == from_user_id and self._items[key].spec.name == name
        ]
        async with self._lock:
            for key in moved_keys:
                for history_key in list(self._history):
                    if history_key[:3] == key:
                        self._history[(tenant_id, to_user_id, key[2], history_key[3])] = (
                            self._history.pop(history_key)
                        )
                draft = self._items.pop(key)
                self._items[(tenant_id, to_user_id, key[2])] = draft.model_copy(
                    update={
                        "created_by": to_user_id,
                        "updated_by": to_user_id,
                    }
                )
        return len(moved_keys)
