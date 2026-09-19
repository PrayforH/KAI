"""Load the Claude provider that cc-switch applied to Claude Code."""

import json
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, SecretStr

from harness.core.models import ModelCompatibility


class CcSwitchConfigError(ValueError):
    """The applied cc-switch Claude configuration cannot start the SDK runtime."""


class CcSwitchClaudeConfig(BaseModel):
    """One resolved model route, ready to hand to a runtime.

    ``provider`` is the control plane's routing identity and ``auth_scheme`` is
    how the credential travels; neither says which wire protocol the route
    speaks. ``api_format`` does, and it is the only field a runtime that speaks
    more than one protocol can dispatch on, so it is carried rather than
    re-derived from the catalog.
    """

    model_config = ConfigDict(frozen=True)

    route_id: str | None = None
    base_url: str
    model: str
    provider: Literal["new-api", "anthropic"]
    credential: SecretStr
    auth_scheme: Literal["bearer", "x-api-key"] | None = None
    api_format: Literal[
        "anthropic_compatible",
        "openai_compatible",
        "openai_images",
        "openai_videos",
    ] | None = None
    compatibility: ModelCompatibility = ModelCompatibility.FULL
    capabilities: frozenset[str] = frozenset({"streaming", "tool_use"})

    @property
    def resolved_auth_scheme(self) -> Literal["bearer", "x-api-key"]:
        if self.auth_scheme is not None:
            return self.auth_scheme
        return "bearer" if self.provider == "new-api" else "x-api-key"


def load_cc_switch_claude_config(path: str | Path) -> CcSwitchClaudeConfig:
    settings_path = Path(path).expanduser()
    try:
        raw_payload: object = json.loads(settings_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CcSwitchConfigError(
            f"cc-switch Claude settings file not found: {settings_path}"
        ) from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CcSwitchConfigError(
            f"cc-switch Claude settings must be valid JSON: {settings_path}"
        ) from error

    if not isinstance(raw_payload, dict):
        raise CcSwitchConfigError("cc-switch Claude settings must contain an env object")
    payload = cast(dict[str, object], raw_payload)
    raw_env = payload.get("env")
    if not isinstance(raw_env, dict):
        raise CcSwitchConfigError("cc-switch Claude settings must contain an env object")
    env = cast(dict[str, object], raw_env)
    base_url = _non_empty(env.get("ANTHROPIC_BASE_URL"))
    model = _non_empty(env.get("ANTHROPIC_MODEL")) or _non_empty(
        env.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
    )
    auth_token = _non_empty(env.get("ANTHROPIC_AUTH_TOKEN"))
    api_key = _non_empty(env.get("ANTHROPIC_API_KEY"))

    if base_url is None:
        raise CcSwitchConfigError("cc-switch Claude settings are missing the base URL")
    if model is None:
        raise CcSwitchConfigError("cc-switch Claude settings are missing the model")
    if auth_token is None and api_key is None:
        raise CcSwitchConfigError("cc-switch Claude settings are missing a credential")

    return CcSwitchClaudeConfig(
        base_url=base_url,
        model=model,
        provider="new-api" if auth_token is not None else "anthropic",
        credential=SecretStr(auth_token or api_key or ""),
        auth_scheme="bearer" if auth_token is not None else "x-api-key",
    )


def _non_empty(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
