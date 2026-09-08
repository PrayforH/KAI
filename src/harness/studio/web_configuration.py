"""Per-user web preferences and encrypted search credentials, shared by API and workers."""

import json
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict, cast

from pydantic import Field, SecretStr

from harness.core.models import ExecutionIdentity
from harness.runtime.web_tools import PublicWebClient, WebAccessError
from harness.studio.mcp_credential_store import McpCredentialService, StoredMcpCredential
from harness.studio.models import StudioModel

REFERENCE = "platform-web-settings"
Provider = Literal["platform", "minimax", "tavily"]


class SavedWebConfiguration(TypedDict):
    enabled: bool
    provider: Provider
    keys: dict[str, str]


class ConfigureWebRequest(StudioModel):
    enabled: bool = True
    provider: Provider = "platform"
    api_key: SecretStr | None = Field(default=None, alias="apiKey", max_length=16384)
    clear_key: bool = Field(default=False, alias="clearKey")


class WebConfiguration(StudioModel):
    enabled: bool
    effective_enabled: bool = Field(alias="effectiveEnabled")
    platform_enabled: bool = Field(alias="platformEnabled")
    provider: Provider
    platform_provider: Literal["minimax", "tavily"] = Field(alias="platformProvider")
    credential_configured: bool = Field(alias="credentialConfigured")
    personal_key_configured: bool = Field(alias="personalKeyConfigured")


class WebConfigurationService:
    def __init__(
        self,
        credentials: McpCredentialService,
        *,
        enabled: bool = True,
        provider: Literal["minimax", "tavily"] = "tavily",
        api_key: str = "",
    ) -> None:
        self.credentials = credentials
        self.enabled = enabled
        self.provider: Literal["minimax", "tavily"] = provider
        self._api_key = api_key

    async def _read(self, tenant_id: str, user_id: str) -> SavedWebConfiguration:
        stored = await self.credentials.repository.get(tenant_id, user_id, REFERENCE)
        if stored is None:
            return {"enabled": True, "provider": "platform", "keys": {}}
        return cast(
            SavedWebConfiguration,
            json.loads(self.credentials.cipher.decrypt(stored)["configuration"].get_secret_value()),
        )

    async def get(self, tenant_id: str, user_id: str) -> WebConfiguration:
        data = await self._read(tenant_id, user_id)
        provider = self.provider if data["provider"] == "platform" else data["provider"]
        personal = bool(data.get("keys", {}).get(provider))
        return WebConfiguration(
            enabled=data["enabled"],
            effectiveEnabled=self.enabled and data["enabled"],
            platformEnabled=self.enabled,
            provider=data["provider"],
            platformProvider=self.provider,
            credentialConfigured=personal or (provider == self.provider and bool(self._api_key)),
            personalKeyConfigured=personal,
        )

    async def configure(
        self, tenant_id: str, user_id: str, body: ConfigureWebRequest
    ) -> WebConfiguration:
        data = await self._read(tenant_id, user_id)
        provider = self.provider if body.provider == "platform" else body.provider
        keys = dict(data.get("keys", {}))
        if body.clear_key:
            keys.pop(provider, None)
        if body.api_key and body.api_key.get_secret_value().strip():
            keys[provider] = body.api_key.get_secret_value().strip()
        data = SavedWebConfiguration(enabled=body.enabled, provider=body.provider, keys=keys)
        await self.credentials.repository.upsert(
            StoredMcpCredential(
                tenant_id=tenant_id,
                owner_user_id=user_id,
                reference=REFERENCE,
                revision=1,
                key_names=("configuration",),
                ciphertext=self.credentials.cipher.encrypt(
                    tenant_id, user_id, REFERENCE, {"configuration": SecretStr(json.dumps(data))}
                ),
                updated_by=user_id,
                updated_at=datetime.now(UTC),
            )
        )
        if self.credentials.audit:
            await self.credentials.audit.record(
                tenant_id=tenant_id,
                user_id=user_id,
                action="web.configuration.update",
                resource_type="web_configuration",
                resource_id=user_id,
                details={"enabled": body.enabled, "provider": body.provider},
            )
        return await self.get(tenant_id, user_id)

    async def client(self, identity: ExecutionIdentity) -> PublicWebClient:
        data = await self._read(identity.tenant_id, identity.user_id)
        if not self.enabled or not data["enabled"]:
            raise WebAccessError("已在个人设置中关闭联网。可开启后重试；无需重新发布智能体。")
        provider = self.provider if data["provider"] == "platform" else data["provider"]
        key = data.get("keys", {}).get(provider, "")
        if not key and provider == self.provider:
            key = self._api_key
        if not key and provider == "tavily":
            stored = await self.credentials.repository.get(
                identity.tenant_id, identity.user_id, "tavily-readonly"
            )
            if stored:
                value = self.credentials.cipher.decrypt(stored).get("api_key")
                key = value.get_secret_value() if value else ""

        async def get_key() -> str:
            return key

        return PublicWebClient(get_key, provider)


class UserWebClient(PublicWebClient):
    """Recheck persisted switches on every call, including calls in an active run."""

    def __init__(self, service: WebConfigurationService, identity: ExecutionIdentity) -> None:
        self.service = service
        self.identity = identity

    async def search(self, query: str) -> dict[str, Any]:
        return await (await self.service.client(self.identity)).search(query)

    async def fetch(self, url: str) -> dict[str, Any]:
        return await (await self.service.client(self.identity)).fetch(url)
