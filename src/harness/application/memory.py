"""Versioned, user-scoped memory application service."""

import hashlib

from harness.application.types import Clock
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import ExecutionIdentity, UserMemory
from harness.core.ports import UserMemoryRepository
from harness.memory_bank.models import MemorySourceKind
from harness.memory_bank.service import MemoryBankService


class UserMemoryService:
    def __init__(
        self,
        repository: UserMemoryRepository,
        *,
        clock: Clock,
        projection_limit: int = 4_000,
        content_limit: int = 20_000,
        max_retries: int = 3,
        memory_bank: MemoryBankService | None = None,
    ) -> None:
        if projection_limit < 1 or content_limit < projection_limit:
            raise ValueError("invalid user memory limits")
        if max_retries < 1:
            raise ValueError("max_retries must be positive")
        self._repository = repository
        self._clock = clock
        self._projection_limit = projection_limit
        self._content_limit = content_limit
        self._max_retries = max_retries
        self._memory_bank = memory_bank

    async def get(self, identity: ExecutionIdentity) -> UserMemory | None:
        return await self._repository.get(identity.tenant_id, identity.user_id, identity.agent_name)

    async def projection(self, identity: ExecutionIdentity, query: str = "") -> str:
        memory = await self.get(identity)
        if self._memory_bank is not None:
            # Old whole-text memory becomes explicit import proposals once. Stable
            # IDs also prevent a rejected/deleted import being resurrected next run.
            if memory is not None and identity.resolved_agent_owner_user_id == identity.user_id:
                for offset in range(0, len(memory.content), 4000):
                    key = hashlib.sha256(
                        f"{identity.tenant_id}\0{identity.user_id}\0{identity.agent_name}\0{memory.version}\0{offset}".encode()
                    ).hexdigest()
                    entry_id = f"legacy_{key}"
                    try:
                        await self._memory_bank.repository.get_entry(
                            identity.tenant_id, identity.user_id, entry_id
                        )
                    except NotFoundError:
                        try:
                            await self._memory_bank.propose(
                                tenant_id=identity.tenant_id,
                                user_id=identity.user_id,
                                agent_name=identity.agent_name,
                                content=memory.content[offset : offset + 4000],
                                source_kind=MemorySourceKind.IMPORT,
                                source_label="旧版记忆迁移（请确认）",
                                confidence=0.7,
                                entry_id=entry_id,
                            )
                        except ConflictError:
                            pass
            return await self._memory_bank.projection(
                identity,
                query=query,
                char_budget=self._projection_limit,
            )
        return ("" if memory is None else memory.content)[: self._projection_limit]

    async def update(
        self,
        identity: ExecutionIdentity,
        content: str,
        *,
        expected_version: int | None = None,
    ) -> UserMemory:
        normalized = content.strip()
        if not normalized:
            raise ValueError("user memory content must be non-empty")
        if len(normalized) > self._content_limit:
            raise ValueError("user memory content exceeds limit")
        if expected_version is not None and expected_version < 0:
            raise ValueError("expected_version must be non-negative")

        for _attempt in range(self._max_retries):
            current = await self.get(identity)
            if current is None:
                if expected_version not in (None, 0):
                    raise ValueError("user memory version conflict")
                created = UserMemory(
                    tenant_id=identity.tenant_id,
                    user_id=identity.user_id,
                    agent_name=identity.agent_name,
                    content=normalized,
                    version=1,
                    updated_at=self._clock(),
                )
                try:
                    await self._repository.add(created)
                    return created
                except ConflictError:
                    if expected_version == 0:
                        raise ValueError("user memory version conflict") from None
                    continue

            if expected_version is not None and current.version != expected_version:
                raise ValueError("user memory version conflict")
            updated = current.model_copy(
                update={
                    "content": normalized,
                    "version": current.version + 1,
                    "updated_at": self._clock(),
                }
            )
            if await self._repository.compare_and_set(current.version, updated):
                return updated
            if expected_version is not None:
                raise ValueError("user memory version conflict")

        raise ConflictError("user memory update retry limit exceeded")
