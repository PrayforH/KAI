from importlib import import_module
from typing import Any

import pytest
import sqlalchemy as sa


class _StatefulInspector:
    """Pre-0023 schema during upgrade, post-0023 schema during downgrade."""

    def __init__(self) -> None:
        self.post = False

    def has_table(self, table: str) -> bool:
        return table in {
            "agent_versions",
            "agent_drafts",
            "shared_agent_versions",
            "workspace_agents",
            "agent_releases",
            "agent_acls",
        }

    def get_columns(self, table: str) -> list[dict[str, str]]:
        columns = {
            "agent_versions": (
                ["tenant_id", "owner_user_id", "name", "version", "agent_id"]
                if self.post
                else ["tenant_id", "owner_user_id", "name", "version"]
            ),
            "agent_drafts": (
                ["tenant_id", "owner_user_id", "draft_id", "agent_id", "space_id"]
                if self.post
                else ["tenant_id", "owner_user_id", "draft_id"]
            ),
            "shared_agent_versions": [
                "tenant_id",
                "space_id",
                "agent_owner_user_id",
                "agent_name",
                "agent_version",
            ],
        }
        return [{"name": name} for name in columns.get(table, [])]


class _FakeBind:
    """Executes recorded SQL statements through a recording connection."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, statement: sa.TextClause, *args: Any, **kwargs: Any) -> Any:
        return self._connection.execute(statement, *args, **kwargs)


def _run_migration(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str]]:
    migration = import_module("migrations.versions.0023_workspace_agents")
    executed: list[str] = []
    dropped: list[str] = []
    inspector = _StatefulInspector()

    class _FakeConnection:
        def execute(self, statement: sa.TextClause, *args: Any, **kwargs: Any) -> Any:
            executed.append(str(statement))
            return None

    class _FakeOp:
        def get_bind(self) -> Any:
            return _FakeBind(_FakeConnection())

        def add_column(self, table: str, _column: Any) -> None:
            executed.append(f"add_column {table}")
            if table == "agent_drafts":
                # All identity columns are now in place; the migration's
                # NOT NULL tightening sees the post-backfill schema.
                inspector.post = True

        def alter_column(self, table: str, _column: Any, **kwargs: Any) -> None:
            executed.append(f"alter_column {table} nullable={kwargs.get('nullable')}")

        def drop_column(self, table: str, column: str) -> None:
            executed.append(f"drop_column {table}.{column}")

    class _FakeMetadataTable:
        def __init__(self, name: str) -> None:
            self.name = name

        def create(self, bind: Any, checkfirst: bool = False) -> None:
            executed.append(f"create {self.name}")

        def drop(self, bind: Any, checkfirst: bool = False) -> None:
            dropped.append(self.name)

    class _FakeMetadata:
        def __init__(self) -> None:
            self.tables = {
                name: _FakeMetadataTable(name)
                for name in (
                    "workspace_agents",
                    "agent_releases",
                    "agent_acls",
                )
            }

    monkeypatch.setattr(migration, "op", _FakeOp())
    monkeypatch.setattr(migration.Base, "metadata", _FakeMetadata())

    def inspect(_bind: object) -> _StatefulInspector:
        return inspector

    monkeypatch.setattr(migration.sa, "inspect", inspect)
    migration.upgrade()
    inspector.post = True
    migration.downgrade()
    return executed, dropped


def test_0023_creates_tables_backfills_identities_and_reverses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed, dropped = _run_migration(monkeypatch)

    # Tables are created before columns are added.
    assert "create workspace_agents" in executed
    assert "create agent_releases" in executed
    assert "create agent_acls" in executed

    # Envelope columns are added for legacy tables.
    assert "add_column agent_versions" in executed
    assert "add_column agent_drafts" in executed

    # Both personal backfills (versions + drafts) run, then the legacy shared
    # grants are migrated into workspace Agents and Releases.
    sql = "\n".join(executed)
    assert "FROM agent_versions" in sql
    assert "FROM agent_drafts" in sql
    assert "FROM shared_agent_versions" in sql
    assert "INSERT INTO agent_releases" in sql
    assert "jsonb_build_object(" in sql
    assert "'agentId'" in sql

    # The identity columns are tightened to NOT NULL after backfill.
    assert "alter_column agent_versions nullable=False" in executed
    assert "alter_column agent_drafts nullable=False" in executed

    # Downgrade drops the new tables and identity columns.
    assert set(dropped) == {"workspace_agents", "agent_releases", "agent_acls"}
    assert "drop_column agent_versions.agent_id" in executed
    assert "drop_column agent_drafts.agent_id" in executed
    assert "drop_column agent_drafts.space_id" in executed
