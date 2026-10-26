"""Transport-neutral, validated records; never persist SDK payloads or credentials."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from harness.assistant.controls import ControlIntent
from harness.core.models import InputArtifact
from harness.studio.models import StudioModel


class FeishuSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_FEISHU_", env_file=".env", extra="ignore")
    enabled: bool = False
    app_id: str = ""
    app_secret: SecretStr = SecretStr("")
    public_base_url: str = ""
    max_resource_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    vision_model_route: str = ""
    receiver_label: str = Field(default="当前服务器", max_length=80)
    configuration_revision: int = 0

    @property
    def configured(self) -> bool:
        # Long connections and card callbacks do not need a public web origin.
        # Without one, cards omit their optional link back to the web console.
        return bool(self.app_id and self.app_secret.get_secret_value())

    @model_validator(mode="after")
    def valid_deployment(self) -> FeishuSettings:
        from urllib.parse import urlsplit

        if self.public_base_url:
            value = urlsplit(self.public_base_url)
            if (
                value.scheme != "https"
                or not value.hostname
                or value.username
                or value.password
                or value.query
                or value.fragment
                or value.path not in {"", "/"}
            ):
                raise ValueError(
                    "Feishu public base URL must be an HTTPS origin without credentials"
                )
        if self.enabled and not self.configured:
            raise ValueError("Enabled Feishu requires App ID and App Secret")
        return self


class Actor(StudioModel):
    app_id: str = Field(min_length=1, max_length=128)
    tenant_key: str = Field(min_length=1, max_length=128)
    open_id: str = Field(min_length=1, max_length=128)


class MessageResource(StudioModel):
    kind: Literal["image", "file", "audio"]
    key: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(default="", max_length=255)
    duration_ms: int = Field(default=0, ge=0, le=86_400_000)


class PreparedMessage(StudioModel):
    text: str = Field(max_length=16_000)
    artifacts: list[InputArtifact] = Field(default_factory=list[InputArtifact], max_length=10)
    model_route: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,63}$")


class Incoming(StudioModel):
    actor: Actor
    key: str = Field(min_length=1, max_length=256)
    kind: Literal["message", "bind", "action"]
    text: str = Field(default="", max_length=8000)
    # A bind event contains a digest, never the raw short-lived credential.
    code_digest: str = ""
    action_digest: str = ""
    message_id: str = Field(default="", max_length=128)
    resources: list[MessageResource] = Field(default_factory=list[MessageResource], max_length=10)
    prepared: PreparedMessage | None = None
    control: ControlIntent | None = None


class Binding(StudioModel):
    binding_id: str
    actor: Actor
    tenant_id: str
    user_id: str
    notifications: bool
    created_at: AwareDatetime
    revoked_at: AwareDatetime | None = None


class BindingCode(StudioModel):
    code: str
    expires_at: AwareDatetime = Field(alias="expiresAt")


class ConnectionStatus(StudioModel):
    configured: bool
    enabled: bool
    receiver_online: bool = Field(alias="receiverOnline")
    receiver_state: str = Field(alias="receiverState")
    last_event_at: AwareDatetime | None = Field(default=None, alias="lastEventAt")
    bound: bool
    notifications: bool
    bound_at: AwareDatetime | None = Field(default=None, alias="boundAt")
    pending_deliveries: int = Field(default=0, alias="pendingDeliveries")
    failed_deliveries: int = Field(default=0, alias="failedDeliveries")


class NotificationPreference(StudioModel):
    enabled: bool


class MessageContent(StudioModel):
    msg_type: Literal["text", "interactive"]
    content: dict[str, Any]
