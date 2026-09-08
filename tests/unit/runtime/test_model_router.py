import pytest

from harness.core.errors import ConflictError
from harness.core.models import ModelCompatibility, ModelRoute
from harness.runtime.claude_sdk import permission_mode_for_route
from harness.runtime.model_router import ModelRouter


def route(
    route_id: str,
    provider: str,
    capabilities: frozenset[str],
    compatibility: ModelCompatibility = ModelCompatibility.FULL,
) -> ModelRoute:
    return ModelRoute(
        route_id=route_id,
        provider=provider,
        base_url=f"https://{provider}.example/v1",
        model="claude-sonnet-4-6",
        compatibility=compatibility,
        capabilities=capabilities,
    )


def test_new_api_primary_is_selected_when_capabilities_match() -> None:
    router = ModelRouter(
        [
            route("new-api-default", "new-api", frozenset({"streaming", "tool_use"})),
            route("anthropic-official", "anthropic", frozenset({"streaming"})),
        ]
    )

    result = router.resolve(
        "new-api-default",
        required_capabilities=frozenset({"streaming", "tool_use"}),
        fallback_route_id="anthropic-official",
    )

    assert result.route.route_id == "new-api-default"
    assert result.used_fallback is False
    assert result.event_payload == {
        "route_id": "new-api-default",
        "provider": "new-api",
        "model": "claude-sonnet-4-6",
        "compatibility": "full",
        "capabilities": ["streaming", "tool_use"],
        "used_fallback": False,
    }


def test_explicit_fallback_is_used_for_incompatible_primary() -> None:
    router = ModelRouter(
        [
            route(
                "new-api-default",
                "new-api",
                frozenset({"streaming"}),
                ModelCompatibility.UNSUPPORTED,
            ),
            route(
                "anthropic-official",
                "anthropic",
                frozenset({"streaming", "tool_use"}),
            ),
        ]
    )

    result = router.resolve(
        "new-api-default",
        required_capabilities=frozenset({"tool_use"}),
        fallback_route_id="anthropic-official",
    )

    assert result.route.route_id == "anthropic-official"
    assert result.used_fallback is True


def test_missing_capability_without_explicit_fallback_fails() -> None:
    router = ModelRouter([route("new-api-default", "new-api", frozenset({"streaming"}))])

    with pytest.raises(ConflictError, match="required capabilities"):
        router.resolve(
            "new-api-default",
            required_capabilities=frozenset({"tool_use"}),
        )


@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "claude-opus-4-6", "claude-opus-4-7"])
def test_official_supported_claude_route_uses_auto_permissions(model: str) -> None:
    configured = ModelRoute(
        route_id="anthropic-official",
        provider="anthropic",
        base_url="https://api.anthropic.com/",
        model=model,
        compatibility=ModelCompatibility.FULL,
        capabilities=frozenset({"streaming", "tool_use"}),
    )

    assert permission_mode_for_route(configured) == "auto"


@pytest.mark.parametrize(
    ("provider", "base_url", "model"),
    [
        ("new-api", "https://api.anthropic.com", "claude-sonnet-4-6"),
        ("anthropic", "https://gateway.example/v1", "claude-sonnet-4-6"),
        ("anthropic", "https://api.anthropic.com", "claude-haiku-4-5"),
    ],
)
def test_noneligible_route_keeps_harness_permissions(
    provider: str,
    base_url: str,
    model: str,
) -> None:
    configured = ModelRoute(
        route_id="route-a",
        provider=provider,
        base_url=base_url,
        model=model,
        compatibility=ModelCompatibility.FULL,
        capabilities=frozenset({"streaming", "tool_use"}),
    )

    assert permission_mode_for_route(configured) == "dontAsk"
