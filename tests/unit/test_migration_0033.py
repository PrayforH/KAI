from importlib import import_module

import pytest

from harness.storage.models import SandboxLeaseRow


def test_sandbox_lease_migration_creates_and_drops_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = import_module("migrations.versions.0033_sandbox_leases")
    calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(
        SandboxLeaseRow.__table__,
        "create",
        lambda bind, checkfirst=False: calls.append(("create", checkfirst)),
    )
    monkeypatch.setattr(
        SandboxLeaseRow.__table__,
        "drop",
        lambda bind, checkfirst=False: calls.append(("drop", checkfirst)),
    )

    migration.upgrade()
    migration.downgrade()

    assert calls == [("create", True), ("drop", True)]
    assert migration.down_revision == "0032"
