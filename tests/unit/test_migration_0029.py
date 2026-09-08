from importlib import import_module

import pytest


def test_agent_catalog_projection_trigger_preserves_rollback_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = import_module(
        "migrations.versions.0029_agent_catalog_projection_compatibility"
    )
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()
    migration.downgrade()

    sql = " ".join(statements)
    assert "CREATE OR REPLACE FUNCTION sync_agent_version_catalog_projection" in sql
    assert "BEFORE INSERT OR UPDATE OF payload ON agent_versions" in sql
    assert "IF NEW.status IS NULL" in sql
    assert "IF NEW.catalog_manifest IS NULL" in sql
    assert "DROP TRIGGER IF EXISTS trg_agent_versions_catalog_projection" in sql
    assert "DROP FUNCTION IF EXISTS sync_agent_version_catalog_projection()" in sql
