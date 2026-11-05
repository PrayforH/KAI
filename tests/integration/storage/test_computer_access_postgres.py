"""Canonical ownership and execution fencing against a real PostgreSQL database."""

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import Table, delete, text, update

from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD
from harness.assistant.models import Objective, Specialist, SpecialistStatus
from harness.assistant.storage import ObjectiveRow, SpecialistRow
from harness.computer.access import CanonicalComputerAccess
from harness.computer.models import ComputerLeaseLostError
from harness.computer.registry import ComputerRegistry
from harness.core.models import AguiThreadBinding, Run, RunStatus, Session
from harness.sandbox.lease import SandboxLeaseService
from harness.storage.models import (
    AguiThreadBindingRow,
    SandboxLeaseRow,
    SessionRow,
    TenantMembershipRow,
    UserRow,
)
from harness.storage.repositories import PostgresRunRepository
from harness.storage.sandbox_lease_repository import PostgresSandboxLeaseRepository
from tests.integration.storage.conftest import DatabaseFixture

NOW = datetime.now(UTC)


async def seed(database: DatabaseFixture):
    _, sessions = database
    user, tenant, handle = "fixture-user", "fixture-tenant", "computer-run-" + "a" * 32
    session = Session(
        tenant_id=tenant,
        user_id=user,
        session_id="fixture-session",
        agent_name="assistant",
        agent_version="1",
        created_at=NOW,
    )
    binding = AguiThreadBinding(
        tenant_id=tenant,
        user_id=user,
        thread_id=PERSONAL_ASSISTANT_THREAD,
        session_id=session.session_id,
        created_at=NOW,
        updated_at=NOW,
    )
    async with sessions.begin() as db:
        db.add(
            UserRow(
                user_id=user,
                email="fixture@invalid.local",
                display_name="fixture",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        db.add(TenantMembershipRow(tenant_id=tenant, user_id=user, role="owner", created_at=NOW))
        db.add(
            SessionRow(
                tenant_id=tenant,
                session_id=session.session_id,
                user_id=user,
                payload=session.model_dump(mode="json"),
            )
        )
        db.add(
            AguiThreadBindingRow(
                tenant_id=tenant,
                user_id=user,
                thread_id=binding.thread_id,
                session_id=binding.session_id,
                payload=binding.model_dump(mode="json"),
            )
        )
    runs = PostgresRunRepository(sessions, fence_computers=True)
    run = Run(
        tenant_id=tenant,
        session_id=session.session_id,
        run_id="fixture-run",
        status=RunStatus.RUNNING,
        fencing_token=2,
        idempotency_key="fixture-key",
        created_at=NOW,
        updated_at=NOW,
    )
    await runs.add(run)
    sandbox_leases = SandboxLeaseService(PostgresSandboxLeaseRepository(sessions))
    sandbox = await sandbox_leases.acquire(
        tenant_id=tenant,
        session_id=session.session_id,
        run_id=run.run_id,
        owner="run-fence:1",
        provider="docker-computer",
        sandbox_id=handle,
    )
    registry = ComputerRegistry(sessions)
    await registry.ensure(tenant, user, provider="docker-computer", image="sha256:" + "a" * 64)
    start = await registry.claim_start(tenant, user, owner=handle)
    access = CanonicalComputerAccess(sessions, {tenant: frozenset({user})})
    computer = await registry.get(tenant, user)
    await access.authorize(computer, start, "ensure")
    computer = await registry.complete_start(start, provider_ref="physical-fixture")
    token = await registry.claim_execution(
        tenant,
        user,
        owner=handle,
        run_id=run.run_id,
        session_id=session.session_id,
        run_fencing_token=run.fencing_token,
    )
    return access, registry, computer, token, runs, run, session, sandbox


@pytest.mark.asyncio
async def test_owned_personal_session_and_current_run_are_authorized(database: DatabaseFixture):
    access, _, computer, token, _, _, session, _ = await seed(database)
    assert await access.is_personal_session(session)
    await access.authorize(computer, token, "exec")
    await access.authorize(computer, token, "renew")
    assert not await access.is_personal_session(session.model_copy(update={"user_id": "other"}))
    assert not await access.is_personal_session(
        session.model_copy(update={"session_id": "ordinary-task"})
    )


@pytest.mark.asyncio
async def test_retained_session_history_remains_owned(database: DatabaseFixture):
    access, _, computer, token, _, _, session, _ = await seed(database)
    _, sessions = database
    async with sessions.begin() as db:
        row = await db.get(
            AguiThreadBindingRow, (session.tenant_id, session.user_id, PERSONAL_ASSISTANT_THREAD)
        )
        assert row is not None
        assert row is not None
        binding = AguiThreadBinding.model_validate(row.payload).model_copy(
            update={"session_id": "newer-session", "previous_session_ids": (session.session_id,)}
        )
        row.session_id = binding.session_id
        row.payload = binding.model_dump(mode="json")
    assert await access.is_personal_session(session)
    await access.authorize(computer, token, "exec")


async def use_specialist_binding(
    database: DatabaseFixture, session: Session, *, enabled: bool | None = True,
) -> Specialist:
    _, sessions = database
    bot = Specialist(
        specialistId="specialist_fixture", tenantId=session.tenant_id, userId=session.user_id,
        name="任意名称", slug="coding", description="", agentName=session.agent_name,
        agentVersion=session.agent_version, status=SpecialistStatus.READY,
        createdAt=NOW, updatedAt=NOW,
        config=({} if enabled is None else
                {"execution_environment": "personal_computer" if enabled else "sandbox"}),
    )
    async with sessions.begin() as db:
        row = await db.get(AguiThreadBindingRow,
                           (session.tenant_id, session.user_id, PERSONAL_ASSISTANT_THREAD))
        assert row is not None
        binding = AguiThreadBinding.model_validate(row.payload).model_copy(
            update={"thread_id": "assistant_" + bot.specialist_id}
        )
        row.thread_id = binding.thread_id
        row.payload = binding.model_dump(mode="json")
        db.add(SpecialistRow(specialist_id=bot.specialist_id, tenant_id=bot.tenant_id,
            user_id=bot.user_id, slug=bot.slug, status=bot.status.value, revision=bot.revision,
            created_at=NOW, updated_at=NOW, payload=bot.model_dump(mode="json", by_alias=True)))
    return bot


@pytest.mark.asyncio
async def test_explicit_sandbox_override_revokes_computer_access_and_keeps_execution_fences(
    database: DatabaseFixture,
) -> None:
    access, _, computer, token, _, _, session, _ = await seed(database)
    await use_specialist_binding(database, session)
    assert await access.is_personal_session(session)
    await access.authorize(computer, token, "exec")
    await access.authorize(computer, token, "renew")
    _, sessions = database
    async with sessions.begin() as db:
        row = await db.get(SpecialistRow, "specialist_fixture")
        assert row is not None
        row.payload = {**row.payload, "config": {"execution_environment": "sandbox"}}
    assert not await access.is_personal_session(session)
    with pytest.raises(ComputerLeaseLostError):
        await access.authorize(computer, token, "exec")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "sandbox", "unknown", "pending-permission", "project"])
async def test_default_bot_inherits_owner_desktop_but_not_task_or_ungranted_capabilities(
    database: DatabaseFixture, change: str | None,
) -> None:
    access, _, computer, token, _, _, session, _ = await seed(database)
    await use_specialist_binding(database, session, enabled=None)
    _, sessions = database
    async with sessions.begin() as db:
        row = await db.get(SpecialistRow, "specialist_fixture")
        assert row is not None
        config = dict(row.payload["config"])
        if change in {"sandbox", "unknown"}:
            config["execution_environment"] = change
        elif change == "pending-permission":
            config["required_local_tools"] = ["use_connected_apps"]
        elif change == "project":
            binding = await db.get(AguiThreadBindingRow,
                (session.tenant_id, session.user_id, "assistant_specialist_fixture"))
            assert binding is not None
            binding.project_id = "ordinary-project"
        row.payload = {**row.payload, "config": config}
    assert await access.is_personal_session(session) is (change is None)
    if change is None:
        await access.authorize(computer, token, "exec")
        await access.authorize(computer, token, "renew")
        assert not await CanonicalComputerAccess(sessions, {}).is_personal_session(session)
    else:
        with pytest.raises(ComputerLeaseLostError):
            await access.authorize(computer, token, "exec")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", [None, "paused", "unrelated", "other-owner", "project", "managed"]
)
async def test_existing_bot_duty_conversation_keeps_history_and_requires_owned_active_relation(
    database: DatabaseFixture, change: str | None,
) -> None:
    access, _, computer, token, _, _, session, _ = await seed(database)
    bot = await use_specialist_binding(database, session)
    objective = Objective(
        objectiveId="objective_legacy", tenantId=session.tenant_id, userId=session.user_id,
        specialistId=bot.specialist_id, title="Existing duty", instructions="Read-only fixture",
        agentName=session.agent_name, agentVersion=session.agent_version,
        createdAt=NOW, updatedAt=NOW,
    )
    if change == "paused":
        objective = objective.model_copy(update={"status": type(objective.status).PAUSED})
    elif change == "unrelated":
        objective = objective.model_copy(update={"specialist_id": None})
    elif change == "other-owner":
        objective = objective.model_copy(update={"user_id": "another-user"})
    elif change == "project":
        objective = objective.model_copy(update={"project_id": "project_fixture"})
    elif change == "managed":
        objective = objective.model_copy(update={"managed_thread_id": "ordinary-task"})
    _, sessions = database
    async with sessions.begin() as db:
        row = await db.get(AguiThreadBindingRow,
            (session.tenant_id, session.user_id, "assistant_" + bot.specialist_id))
        assert row is not None
        binding = AguiThreadBinding.model_validate(row.payload).model_copy(
            update={"thread_id": "assistant_" + objective.objective_id})
        row.thread_id, row.payload = binding.thread_id, binding.model_dump(mode="json")
        db.add(ObjectiveRow(objective_id=objective.objective_id, tenant_id=objective.tenant_id,
            user_id=objective.user_id, status=objective.status.value, revision=1,
            created_at=NOW, updated_at=NOW,
            payload=objective.model_dump(mode="json", by_alias=True)))
    assert await access.is_personal_session(session) is (change is None)
    if change is None:
        await access.authorize(computer, token, "exec")
        await access.authorize(computer, token, "renew")
    else:
        with pytest.raises(ComputerLeaseLostError):
            await access.authorize(computer, token, "exec")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["paused", "archived", "other-owner", "other-tenant", "project"])
async def test_specialist_computer_access_rechecks_owner_lifecycle_and_task_isolation(
    database: DatabaseFixture, change: str | None,
) -> None:
    access, _, computer, token, _, _, session, _ = await seed(database)
    await use_specialist_binding(database, session)
    _, sessions = database
    async with sessions.begin() as db:
        row = await db.get(SpecialistRow, "specialist_fixture")
        assert row is not None
        if change in {"paused", "archived"}:
            row.status = change
            row.payload = {**row.payload, "status": change}
        elif change == "other-owner":
            row.user_id = "someone-else"
        elif change == "other-tenant":
            row.tenant_id = "another-tenant"
        else:
            binding = await db.get(AguiThreadBindingRow,
                (session.tenant_id, session.user_id, "assistant_specialist_fixture"))
            assert binding is not None
            binding.project_id = "ordinary-project"
    assert not await access.is_personal_session(session)
    with pytest.raises(ComputerLeaseLostError):
        await access.authorize(computer, token, "exec")


@pytest.mark.parametrize(
    "change",
    [
        "disabled",
        "membership",
        "archived",
        "session-owner",
        "run-cancelling",
        "sandbox-expired",
        "computer-expired",
        "owner-not-enabled",
        "new-worker",
    ],
)
@pytest.mark.asyncio
async def test_revocation_or_changed_execution_refuses_commands_and_allows_cleanup(
    database: DatabaseFixture,
    change: str,
):
    access, _, computer, token, _, _, session, sandbox = await seed(database)
    _, sessions = database
    async with sessions.begin() as db:
        if change == "disabled":
            await db.execute(update(UserRow).values(disabled=True))
        elif change == "membership":
            await db.execute(delete(TenantMembershipRow))
        elif change == "archived":
            row = await db.get(
                AguiThreadBindingRow,
                (session.tenant_id, session.user_id, PERSONAL_ASSISTANT_THREAD),
            )
            assert row is not None
            row.payload = {**row.payload, "archived_at": NOW.isoformat()}
        elif change == "session-owner":
            await db.execute(update(SessionRow).values(user_id="other-user"))
        elif change == "run-cancelling":
            await db.execute(text("UPDATE runs SET status='cancelling'"))
        elif change == "sandbox-expired":
            await db.execute(update(SandboxLeaseRow).values(expires_at=NOW - timedelta(seconds=1)))
        elif change == "computer-expired":
            await db.execute(
                text(
                    "UPDATE computer_execution_leases SET "
                    "created_at=clock_timestamp()-interval '2 hours', "
                    "expires_at=clock_timestamp()-interval '1 hour' WHERE lease_id=:id"
                ),
                {"id": token.lease_id},
            )
        elif change == "new-worker":
            db.add(
                SandboxLeaseRow(
                    tenant_id=sandbox.tenant_id,
                    lease_id="new-worker-lease",
                    session_id=sandbox.session_id,
                    run_id=sandbox.run_id,
                    owner="new-worker",
                    epoch=sandbox.epoch + 1,
                    provider=sandbox.provider,
                    sandbox_id="new-handle",
                    state="active",
                    created_at=NOW,
                    renewed_at=NOW,
                    expires_at=NOW + timedelta(hours=1),
                )
            )
        elif change == "owner-not-enabled":
            access = CanonicalComputerAccess(sessions, {})
    with pytest.raises(ComputerLeaseLostError):
        await access.authorize(computer, token, "exec")
    with pytest.raises(ComputerLeaseLostError):
        await access.authorize(computer, token, "renew")
    if change in {"disabled", "membership", "archived", "session-owner"}:
        with pytest.raises(ComputerLeaseLostError):
            await access.is_personal_session(session)
    # The control entrypoint still validates the live computer lease before
    # invoking this authorizer; cleanup alone grants no command/file access.
    await access.authorize(computer, token, "deactivate")
    await access.authorize(computer, token, "reset")


@pytest.mark.asyncio
async def test_normal_transitions_keep_attempt_but_reclaim_fences_old_worker(
    database: DatabaseFixture,
):
    access, _, computer, token, runs, run, _, _ = await seed(database)
    waiting = run.model_copy(update={"status": RunStatus.WAITING_APPROVAL, "fencing_token": 3})
    assert await runs.compare_and_set(run.status, waiting)
    await access.authorize(computer, token, "renew")
    with pytest.raises(ComputerLeaseLostError):
        await access.authorize(computer, token, "exec")
    resumed = waiting.model_copy(update={"status": RunStatus.RUNNING, "fencing_token": 4})
    assert await runs.compare_and_set(waiting.status, resumed)
    await access.authorize(computer, token, "exec")
    reclaimed = resumed.model_copy(update={"fencing_token": 5})
    assert await runs.compare_and_set(resumed.status, reclaimed)
    with pytest.raises(ComputerLeaseLostError):
        await access.authorize(computer, token, "exec")
    with pytest.raises(ComputerLeaseLostError):
        await access.authorize(computer, token, "renew")
    await access.authorize(computer, token, "deactivate")


@pytest.mark.asyncio
async def test_failed_run_cas_does_not_advance_computer_permission(database: DatabaseFixture):
    access, _, computer, token, runs, run, _, _ = await seed(database)
    stale = run.model_copy(update={"status": RunStatus.WAITING_APPROVAL, "fencing_token": 3})
    assert not await runs.compare_and_set(RunStatus.QUEUED, stale)
    await access.authorize(computer, token, "exec")


@pytest.mark.asyncio
async def test_feature_disabled_run_repository_does_not_require_computer_schema(
    database: DatabaseFixture,
):
    _, _, _, _, _, run, _, _ = await seed(database)
    engine, sessions = database
    from harness.computer.storage import ComputerLeaseRow

    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: cast(Table, ComputerLeaseRow.__table__).drop(sync))
    runs = PostgresRunRepository(sessions)
    changed = run.model_copy(update={"status": RunStatus.SUCCEEDED, "fencing_token": 3})
    assert await runs.compare_and_set(run.status, changed)


@pytest.mark.asyncio
async def test_revoked_account_is_refused_before_any_docker_request(database: DatabaseFixture):
    import httpx

    from harness.computer.control import DockerComputerControl

    access, registry, _, token, _, _, _, _ = await seed(database)
    _, sessions = database
    async with sessions.begin() as db:
        await db.execute(update(UserRow).values(disabled=True))

    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Revoked access must not reach Docker")

    async with httpx.AsyncClient(
        base_url="http://docker", transport=httpx.MockTransport(unexpected)
    ) as docker:
        controller = DockerComputerControl(registry, docker, authorizer=access)
        with pytest.raises(ComputerLeaseLostError):
            await controller.call(token, "exec", {"argv": ["private-fixture"]})


async def seed_objective_run(database: DatabaseFixture):
    """A background objective run on its own thread, for the same owner."""
    _, sessions = database
    user, tenant, handle = "fixture-user", "fixture-tenant", "computer-run-" + "b" * 32
    objective_id = "objective_" + "f" * 32
    thread_id = "assistant_" + objective_id
    session = Session(
        tenant_id=tenant,
        user_id=user,
        session_id="objective-session",
        agent_name="assistant",
        agent_version="1",
        created_at=NOW,
    )
    binding = AguiThreadBinding(
        tenant_id=tenant,
        user_id=user,
        thread_id=thread_id,
        session_id=session.session_id,
        created_at=NOW,
        updated_at=NOW,
    )
    async with sessions.begin() as db:
        db.add(UserRow(
            user_id=user,
            email="objective@invalid.local",
            display_name="fixture",
            created_at=NOW,
            updated_at=NOW,
        ))
        db.add(TenantMembershipRow(tenant_id=tenant, user_id=user, role="owner", created_at=NOW))
        db.add(SessionRow(
            tenant_id=tenant,
            session_id=session.session_id,
            user_id=user,
            payload=session.model_dump(mode="json"),
        ))
        db.add(AguiThreadBindingRow(
            tenant_id=tenant,
            user_id=user,
            thread_id=binding.thread_id,
            session_id=binding.session_id,
            payload=binding.model_dump(mode="json"),
        ))
        db.add(ObjectiveRow(
            objective_id=objective_id,
            tenant_id=tenant,
            user_id=user,
            status="active",
            revision=1,
            created_at=NOW,
            updated_at=NOW,
            payload={"threadId": thread_id, "status": "active"},
        ))
    runs = PostgresRunRepository(sessions, fence_computers=True)
    run = Run(
        tenant_id=tenant,
        session_id=session.session_id,
        run_id="objective-run",
        status=RunStatus.RUNNING,
        fencing_token=4,
        idempotency_key="objective-key",
        created_at=NOW,
        updated_at=NOW,
    )
    await runs.add(run)
    sandbox_leases = SandboxLeaseService(PostgresSandboxLeaseRepository(sessions))
    await sandbox_leases.acquire(
        tenant_id=tenant,
        session_id=session.session_id,
        run_id=run.run_id,
        owner="run-fence:2",
        provider="docker-computer",
        sandbox_id=handle,
    )
    registry = ComputerRegistry(sessions)
    await registry.ensure(tenant, user, provider="docker-computer", image="sha256:" + "a" * 64)
    start = await registry.claim_start(tenant, user, owner=handle)
    await registry.complete_start(start, provider_ref="physical-fixture")
    token = await registry.claim_execution(
        tenant,
        user,
        owner=handle,
        run_id=run.run_id,
        session_id=session.session_id,
        run_fencing_token=run.fencing_token,
    )
    return access_for(sessions), registry, token, objective_id


def access_for(sessions) -> CanonicalComputerAccess:
    return CanonicalComputerAccess(sessions, {"fixture-tenant": frozenset({"fixture-user"})})


@pytest.mark.asyncio
async def test_a_live_objective_inherits_the_narrow_computer_capability(
    database: DatabaseFixture,
):
    access, registry, token, objective_id = await seed_objective_run(database)
    computer = await registry.get("fixture-tenant", "fixture-user")
    await access.authorize(computer, token, "exec")
    await access.authorize(computer, token, "renew")


@pytest.mark.asyncio
async def test_an_inactive_objective_loses_the_capability_immediately(
    database: DatabaseFixture,
):
    _, sessions = database
    access, registry, token, objective_id = await seed_objective_run(database)
    computer = await registry.get("fixture-tenant", "fixture-user")
    async with sessions.begin() as db:
        await db.execute(
            update(ObjectiveRow)
            .where(ObjectiveRow.objective_id == objective_id)
            .values(status="paused")
        )
    with pytest.raises(ComputerLeaseLostError):
        await access.authorize(computer, token, "exec")


@pytest.mark.asyncio
async def test_a_personal_session_of_another_thread_still_never_routes(
    database: DatabaseFixture,
):
    _, sessions = database
    access, registry, _, _ = await seed_objective_run(database)
    other = Session(
        tenant_id="fixture-tenant",
        user_id="fixture-user",
        session_id="stray-session",
        agent_name="assistant",
        agent_version="1",
        created_at=NOW,
    )
    assert not await access.is_personal_session(other)
