from dataclasses import replace
from typing import cast

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from harness.api.app import create_app
from harness.api.dependencies import build_memory_container


def protected_client() -> httpx.Client:
    container = replace(
        build_memory_container(),
        api_bearer_token=SecretStr("service-token-with-at-least-32-characters"),
    )
    return cast(httpx.Client, TestClient(create_app(container)))


def test_health_check_remains_available_without_api_credential() -> None:
    with protected_client() as client:
        response = client.get("/healthz")

    assert response.status_code == 200


def test_metrics_requires_the_service_credential() -> None:
    token = "service-token-with-at-least-32-characters"
    with protected_client() as client:
        missing = client.get("/metrics")
        authorized = client.get(
            "/metrics", headers={"Authorization": f"Bearer {token}"}
        )

    assert missing.status_code == 401
    assert authorized.status_code == 200
    assert "# HELP harness_api_request_duration_seconds" in authorized.text


def test_v1_boundary_rejects_missing_or_invalid_api_credential() -> None:
    with protected_client() as client:
        missing = client.get(
            "/v1/runs/unknown/events",
            headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "user-a"},
        )
        invalid = client.get(
            "/v1/runs/unknown/events",
            headers={
                "Authorization": "Bearer wrong",
                "X-Tenant-ID": "tenant-a",
                "X-User-ID": "user-a",
            },
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.json()["error"]["code"] == "api_auth_required"
    assert missing.headers["www-authenticate"] == "Bearer"


def test_valid_api_credential_reaches_identity_and_tenant_boundary() -> None:
    token = "service-token-with-at-least-32-characters"
    with protected_client() as client:
        missing_identity = client.get(
            "/v1/runs/unknown/events",
            headers={"Authorization": f"Bearer {token}"},
        )
        authorized = client.get(
            "/v1/runs/unknown/events",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Tenant-ID": "tenant-a",
                "X-User-ID": "user-a",
            },
        )

    assert missing_identity.status_code == 401
    assert missing_identity.json()["error"]["code"] == "identity_required"
    assert authorized.status_code == 404
    assert authorized.json()["error"]["code"] == "not_found"


def test_feishu_identity_controls_require_authentication() -> None:
    with protected_client() as client:
        for method, path, body in (
            ("GET", "status", None),
            ("POST", "binding-code", None),
            ("DELETE", "binding", None),
            ("PUT", "notifications", {"enabled": False}),
        ):
            response = client.request(method, f"/v1/studio/assistant/feishu/{path}", json=body)
            assert response.status_code == 401
