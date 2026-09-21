"""A broken title model must not turn the task list into a retry loop.

``resolve_title`` runs for every binding on every task-list read. When the generator
fails, the stored binding keeps its deterministic title, so nothing the next read sees
has changed: without a cooldown the same thread would queue a model call per refresh.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from harness.adapters.memory import InMemoryAguiThreadBindingRepository
from harness.agui.service import AguiRunService
from harness.api.dependencies import build_memory_container
from harness.core.models import AguiThreadBinding

PROMPTS = ["查一下 174 的部署情况"]


class _UnavailableTitleGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, tenant_id: str, user_id: str, prompts: list[str]) -> str:
        self.calls += 1
        raise RuntimeError("model route is unavailable")


class _WorkingTitleGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, tenant_id: str, user_id: str, prompts: list[str]) -> str:
        self.calls += 1
        return "174 部署核查"


async def _service(generator: object) -> tuple[AguiRunService, InMemoryAguiThreadBindingRepository]:
    container = build_memory_container()
    bindings = InMemoryAguiThreadBindingRepository()
    now = datetime.now(UTC)
    await bindings.add(
        AguiThreadBinding(
            tenant_id="tenant-a",
            user_id="user-a",
            thread_id="thread-a",
            session_id="session-a",
            created_at=now,
            updated_at=now,
        )
    )
    service = AguiRunService(
        sessions=container.sessions,
        runs=container.runs,
        input_artifacts=container.input_artifacts,
        bindings=bindings,
        title_generator=generator,  # type: ignore[arg-type]
    )
    return service, bindings


@pytest.mark.asyncio
async def test_a_failing_generator_is_retried_once_not_on_every_refresh() -> None:
    generator = _UnavailableTitleGenerator()
    service, bindings = await _service(generator)

    for _ in range(3):
        binding = await bindings.get_by_thread("tenant-a", "user-a", "thread-a")
        title = await service.resolve_title(binding, PROMPTS)
        assert title, "the deterministic title stays usable while the model is down"
        await asyncio.sleep(0.05)

    assert generator.calls == 1


@pytest.mark.asyncio
async def test_a_working_generator_still_replaces_the_fallback_title() -> None:
    generator = _WorkingTitleGenerator()
    service, bindings = await _service(generator)

    binding = await bindings.get_by_thread("tenant-a", "user-a", "thread-a")
    await service.resolve_title(binding, PROMPTS)
    await asyncio.sleep(0.05)

    stored = await bindings.get_by_thread("tenant-a", "user-a", "thread-a")
    assert stored.title == "174 部署核查" and stored.title_source == "model"
    assert generator.calls == 1
