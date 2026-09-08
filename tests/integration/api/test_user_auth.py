from dataclasses import replace
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from harness.api.app import create_app
from harness.api.dependencies import build_memory_container
from harness.auth.repositories import InMemoryAuthRepository
from harness.auth.service import AuthService, OAuthProviderConfig


def _client(*, registration: bool = True) -> httpx.Client:
    container = build_memory_container()
    if not registration:
        container = replace(
            container,
            auth=AuthService(
                InMemoryAuthRepository(),
                jwt_secret=SecretStr("test-user-jwt-secret-with-at-least-32-characters"),
                issuer="test-harness",
                audience="test-api",
                access_token_minutes=15,
                refresh_token_days=7,
                allow_registration=False,
                default_tenant_id="local",
                google=OAuthProviderConfig("", SecretStr("")),
                github=OAuthProviderConfig("", SecretStr("")),
            ),
        )
    return cast(httpx.Client, TestClient(create_app(container)))


def _register(client: httpx.Client, email: str = "owner@example.com") -> dict[str, Any]:
    response = client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "SecurePass123",
            "display_name": "Harness Owner",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def test_local_account_registration_login_profile_and_audit() -> None:
    with _client() as client:
        registered = _register(client)
        assert "password_hash" not in registered["user"]
        assert registered["membership"]["role"] == "owner"

        profile = client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {registered['access_token']}"},
        )
        login = client.post(
            "/v1/auth/login",
            json={"email": "OWNER@example.com", "password": "SecurePass123"},
        )
        audit = client.get(
            "/v1/auth/audit-logs",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )

    assert profile.status_code == 200
    assert profile.json()["user"]["email"] == "owner@example.com"
    assert login.status_code == 200
    assert audit.status_code == 200
    assert {entry["action"] for entry in audit.json()} >= {"auth.register", "auth.login"}


def test_user_can_update_profile_and_change_password() -> None:
    with _client() as client:
        session = _register(client)
        headers = {"Authorization": f"Bearer {session['access_token']}"}
        updated = client.patch(
            "/v1/auth/me",
            headers=headers,
            json={"display_name": "Updated Owner"},
        )
        changed = client.post(
            "/v1/auth/password",
            headers=headers,
            json={
                "current_password": "SecurePass123",
                "new_password": "NewSecurePass456",
            },
        )
        old_session = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": session["refresh_token"]},
        )
        old_login = client.post(
            "/v1/auth/login",
            json={"email": "owner@example.com", "password": "SecurePass123"},
        )
        new_login = client.post(
            "/v1/auth/login",
            json={"email": "owner@example.com", "password": "NewSecurePass456"},
        )

    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Updated Owner"
    assert changed.status_code == 204
    assert old_session.status_code == 401
    assert old_login.status_code == 401
    assert new_login.status_code == 200


def test_password_change_rejects_wrong_current_password() -> None:
    with _client() as client:
        session = _register(client)
        response = client.post(
            "/v1/auth/password",
            headers={"Authorization": f"Bearer {session['access_token']}"},
            json={
                "current_password": "WrongPass123",
                "new_password": "NewSecurePass456",
            },
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "password_invalid"


def test_refresh_tokens_rotate_and_reuse_revokes_the_family() -> None:
    with _client() as client:
        session = _register(client)
        first_refresh = session["refresh_token"]
        rotated = client.post("/v1/auth/refresh", json={"refresh_token": first_refresh})
        replay = client.post("/v1/auth/refresh", json={"refresh_token": first_refresh})
        replacement = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": rotated.json()["refresh_token"]},
        )

    assert rotated.status_code == 200
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "refresh_invalid"
    assert replacement.status_code == 401


def test_new_login_immediately_replaces_the_previous_device_session() -> None:
    with _client() as client:
        first = _register(client)
        second = client.post(
            "/v1/auth/login",
            json={"email": "owner@example.com", "password": "SecurePass123"},
        ).json()
        old_access = client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {first['access_token']}"},
        )
        old_refresh = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": first["refresh_token"]},
        )
        current_access = client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {second['access_token']}"},
        )

    assert old_access.status_code == 401
    assert old_access.json()["error"]["code"] == "session_replaced"
    assert old_access.headers["x-harness-auth-error"] == "session_replaced"
    assert old_refresh.status_code == 401
    assert current_access.status_code == 200


def test_registration_can_be_disabled_without_disabling_login_endpoint() -> None:
    with _client(registration=False) as client:
        response = client.post(
            "/v1/auth/register",
            json={
                "email": "member@example.com",
                "password": "SecurePass123",
                "display_name": "Member",
            },
        )
        config = client.get("/v1/auth/config")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "registration_disabled"
    assert config.json()["registration_enabled"] is False


def test_production_rejects_spoofed_identity_headers_but_accepts_signed_user_jwt() -> None:
    container = replace(
        build_memory_container(),
        environment="production",
        api_bearer_token=SecretStr("service-token-with-at-least-32-characters"),
    )
    with cast(httpx.Client, TestClient(create_app(container))) as client:
        session = _register(client)
        spoofed = client.get(
            "/v1/agui/threads",
            headers={"X-Tenant-ID": "local", "X-User-ID": "someone-else"},
        )
        signed = client.get(
            "/v1/agui/threads",
            headers={"Authorization": f"Bearer {session['access_token']}"},
        )

    assert spoofed.status_code == 401
    assert signed.status_code == 200


def test_members_cannot_publish_agent_bundles() -> None:
    with _client() as client:
        _register(client)
        member = _register(client, "member@example.com")
        response = client.post(
            "/v1/agents/bundles",
            headers={
                "Authorization": f"Bearer {member['access_token']}",
                "Content-Type": "application/zip",
            },
            content=b"not-a-bundle",
        )

    assert member["membership"]["role"] == "member"
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_owner_can_list_members_and_update_roles() -> None:
    with _client() as client:
        owner = _register(client)
        member = _register(client, "member@example.com")
        owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
        member_headers = {"Authorization": f"Bearer {member['access_token']}"}

        listed = client.get("/v1/auth/members", headers=owner_headers)
        denied = client.get("/v1/auth/members", headers=member_headers)
        updated = client.patch(
            f"/v1/auth/members/{member['user']['user_id']}",
            headers=owner_headers,
            json={"role": "admin"},
        )
        stale_access = client.get("/v1/auth/me", headers=member_headers)
        refreshed = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": member["refresh_token"]},
        )

    assert listed.status_code == 200
    assert [item["user"]["email"] for item in listed.json()] == [
        "owner@example.com",
        "member@example.com",
    ]
    assert denied.status_code == 403
    assert updated.status_code == 200
    assert updated.json()["membership"]["role"] == "admin"
    assert stale_access.status_code == 401
    assert refreshed.status_code == 200
    assert refreshed.json()["membership"]["role"] == "admin"


def test_scoped_api_key_is_returned_once_enforces_permissions_and_can_be_revoked() -> None:
    with _client() as client:
        owner = _register(client)
        owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
        created = client.post(
            "/v1/auth/api-keys",
            headers=owner_headers,
            json={"name": "只读集成", "permissions": ["tasks:read"]},
        )
        secret = created.json()["secret"]
        listed = client.get("/v1/auth/api-keys", headers=owner_headers)
        api_headers = {"X-API-Key": secret}
        readable = client.get("/v1/agents", headers=api_headers)
        denied = client.post(
            "/v1/sessions",
            headers=api_headers,
            json={"agent_name": "missing-agent", "environment": "production"},
        )
        revoked = client.delete(
            f"/v1/auth/api-keys/{created.json()['api_key']['key_id']}",
            headers=owner_headers,
        )
        after_revoke = client.get("/v1/agents", headers=api_headers)

    assert created.status_code == 201
    assert secret.startswith("ask_")
    assert secret not in listed.text
    assert listed.json()[0]["prefix"] == secret[:12]
    assert readable.status_code == 200
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
    assert after_revoke.status_code == 401
    assert after_revoke.json()["error"]["code"] == "api_key_invalid"


def test_members_cannot_manage_api_keys_and_unknown_permissions_are_rejected() -> None:
    with _client() as client:
        owner = _register(client)
        member = _register(client, "member-api@example.com")
        denied = client.get(
            "/v1/auth/api-keys",
            headers={"Authorization": f"Bearer {member['access_token']}"},
        )
        invalid = client.post(
            "/v1/auth/api-keys",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
            json={"name": "危险权限", "permissions": ["members:write"]},
        )

    assert denied.status_code == 403
    assert invalid.status_code == 409
    assert "unsupported API key permissions" in invalid.json()["error"]["message"]


def test_workspace_cannot_demote_its_last_owner() -> None:
    with _client() as client:
        owner = _register(client)
        response = client.patch(
            f"/v1/auth/members/{owner['user']['user_id']}",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
            json={"role": "member"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "membership_transition_denied"


def test_admin_cannot_assign_owner_or_manage_another_admin() -> None:
    with _client() as client:
        owner = _register(client)
        first = _register(client, "first@example.com")
        second = _register(client, "second@example.com")
        owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
        promoted = client.patch(
            f"/v1/auth/members/{first['user']['user_id']}",
            headers=owner_headers,
            json={"role": "admin"},
        )
        admin_session = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": first["refresh_token"]},
        ).json()
        admin_headers = {"Authorization": f"Bearer {admin_session['access_token']}"}
        assign_owner = client.patch(
            f"/v1/auth/members/{second['user']['user_id']}",
            headers=admin_headers,
            json={"role": "owner"},
        )
        demote_self = client.patch(
            f"/v1/auth/members/{first['user']['user_id']}",
            headers=admin_headers,
            json={"role": "member"},
        )

    assert promoted.status_code == 200
    assert assign_owner.status_code == 409
    assert demote_self.status_code == 409


@pytest.mark.asyncio
async def test_google_oauth_exchange_uses_verified_profile_and_pkce() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "google-access"})
        return httpx.Response(
            200,
            json={
                "sub": "google-subject",
                "email": "sso@example.com",
                "email_verified": True,
                "name": "SSO User",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = AuthService(
        InMemoryAuthRepository(),
        jwt_secret=SecretStr("test-user-jwt-secret-with-at-least-32-characters"),
        issuer="test-harness",
        audience="test-api",
        access_token_minutes=15,
        refresh_token_days=7,
        allow_registration=True,
        default_tenant_id="local",
        google=OAuthProviderConfig("google-client", SecretStr("google-secret")),
        github=OAuthProviderConfig("", SecretStr("")),
        http_client=client,
    )

    session = await service.exchange_oauth_code(
        provider="google",
        code="authorization-code",
        redirect_uri="http://localhost:3000/api/auth/oauth/google/callback",
        code_verifier="v" * 64,
    )
    await client.aclose()

    assert session.user.email == "sso@example.com"
    assert session.user.email_verified is True
    assert len(requests) == 2
    assert b"code_verifier=" + b"v" * 64 in requests[0].content
