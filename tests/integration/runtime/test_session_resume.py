import os

import pytest
from claude_agent_sdk import SessionKey, SessionStoreEntry

from harness.runtime.session_store import PostgresSessionStore
from harness.storage.database import create_database, create_schema, drop_schema

DATABASE_URL = os.getenv(
    "HARNESS_TEST_DATABASE_URL",
    "postgresql+asyncpg://harness:harness@127.0.0.1:5432/harness_test",
)


@pytest.mark.asyncio
async def test_session_and_subagent_transcripts_resume_after_store_recreation() -> None:
    engine, sessions = create_database(DATABASE_URL)
    await drop_schema(engine)
    await create_schema(engine)
    key: SessionKey = {"project_key": "project-a", "session_id": "resume-session"}
    subkey: SessionKey = {**key, "subpath": "subagents/agent-1"}
    main_entries: list[SessionStoreEntry] = [
        {"type": "user", "uuid": "resume-user"},
        {"type": "assistant", "uuid": "resume-assistant"},
    ]
    sub_entries: list[SessionStoreEntry] = [{"type": "assistant", "uuid": "resume-subagent"}]
    first = PostgresSessionStore(sessions, tenant_id="tenant-a")
    await first.delete(key)
    await first.append(key, main_entries)
    await first.append(subkey, sub_entries)

    recreated = PostgresSessionStore(sessions, tenant_id="tenant-a")
    try:
        assert await recreated.load(key) == main_entries
        assert await recreated.load(subkey) == sub_entries
        assert await recreated.list_subkeys(key) == ["subagents/agent-1"]
    finally:
        await recreated.delete(key)
        await engine.dispose()


@pytest.mark.asyncio
async def test_session_store_uses_stable_project_across_run_workspaces() -> None:
    engine, sessions = create_database(DATABASE_URL)
    await drop_schema(engine)
    await create_schema(engine)
    first_key: SessionKey = {
        "project_key": "temporary-run-workspace-a",
        "session_id": "resume-session",
    }
    next_key: SessionKey = {
        "project_key": "temporary-run-workspace-b",
        "session_id": "resume-session",
    }
    entries: list[SessionStoreEntry] = [
        {"type": "user", "uuid": "stable-project-user"},
        {"type": "assistant", "uuid": "stable-project-assistant"},
    ]
    first = PostgresSessionStore(
        sessions,
        tenant_id="tenant-a",
        project_id="harness-session-a",
    )
    await first.delete(first_key)
    await first.append(first_key, entries)

    recreated = PostgresSessionStore(
        sessions,
        tenant_id="tenant-a",
        project_id="harness-session-a",
    )
    try:
        assert await recreated.load(next_key) == entries
    finally:
        await recreated.delete(next_key)
        await engine.dispose()
