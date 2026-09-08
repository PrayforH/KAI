from importlib import import_module

import pytest


def test_personal_agent_current_version_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = import_module(
        "migrations.versions.0027_personal_agent_current_version"
    )
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()
    migration.downgrade()

    sql = " ".join(statements)
    assert "DISTINCT ON (tenant_id, agent_id)" in sql
    assert "agent.scope = 'personal'" in sql
    assert "agent.current_version IS NULL" in sql
    assert "jsonb_build_object" in sql
    assert "currentVersion" in sql
