from typing import cast

import pytest
from fastapi import HTTPException

from harness.api.dependencies import Identity, ensure_permission

STUDIO_PERMISSIONS = (
    "studio:read",
    "studio:write",
    "studio:preview",
    "studio:publish",
    "studio:deploy",
)

ROLE_MATRIX = {
    "viewer": {"studio:read"},
    "member": {"studio:read", "studio:write", "studio:preview", "studio:publish"},
    "admin": set(STUDIO_PERMISSIONS),
    "owner": set(STUDIO_PERMISSIONS),
}


@pytest.mark.parametrize("role", ROLE_MATRIX)
@pytest.mark.parametrize("permission", STUDIO_PERMISSIONS)
def test_studio_role_permission_matrix(role: str, permission: str) -> None:
    identity = Identity("tenant-a", "user-a", roles=frozenset({role}))

    if permission in ROLE_MATRIX[role]:
        ensure_permission(identity, permission)
        return

    with pytest.raises(HTTPException) as captured:
        ensure_permission(identity, permission)

    assert captured.value.status_code == 403
    assert isinstance(captured.value.detail, dict)
    detail = cast(dict[str, object], captured.value.detail)
    assert detail["code"] == "permission_denied"


@pytest.mark.parametrize(
    ("role", "permission", "allowed"),
    [
        ("viewer", "tasks:read", True),
        ("viewer", "tasks:write", False),
        ("member", "tasks:read", True),
        ("member", "tasks:write", True),
        ("admin", "tasks:read", True),
        ("admin", "tasks:write", True),
        ("admin", "agents:publish", True),
        ("owner", "tasks:read", True),
        ("owner", "tasks:write", True),
        ("owner", "agents:publish", True),
    ],
)
def test_existing_task_and_agent_permissions_do_not_regress(
    role: str, permission: str, allowed: bool
) -> None:
    identity = Identity("tenant-a", "user-a", roles=frozenset({role}))

    if allowed:
        ensure_permission(identity, permission)
    else:
        with pytest.raises(HTTPException) as captured:
            ensure_permission(identity, permission)
        assert captured.value.status_code == 403


@pytest.mark.parametrize(
    ("role", "allowed"),
    [("viewer", False), ("member", False), ("admin", True), ("owner", True)],
)
def test_catalog_management_is_admin_only(role: str, allowed: bool) -> None:
    identity = Identity("tenant-a", "user-a", roles=frozenset({role}))
    if allowed:
        ensure_permission(identity, "studio:catalog:write")
    else:
        with pytest.raises(HTTPException) as captured:
            ensure_permission(identity, "studio:catalog:write")
        assert captured.value.status_code == 403
