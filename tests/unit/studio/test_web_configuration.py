import pytest
from pydantic import SecretStr

from harness.core.models import ExecutionIdentity
from harness.runtime.web_tools import WebAccessError
from harness.studio.mcp_credential_store import (
    InMemoryMcpCredentialRepository,
    McpCredentialCipher,
    McpCredentialService,
)
from harness.studio.web_configuration import (
    REFERENCE,
    ConfigureWebRequest,
    UserWebClient,
    WebConfigurationService,
)


def service() -> WebConfigurationService:
    return WebConfigurationService(McpCredentialService(
        InMemoryMcpCredentialRepository(), McpCredentialCipher(SecretStr("test-key"))
    ), provider="minimax", api_key="platform-key")


@pytest.mark.asyncio
async def test_encrypted_keys_are_user_scoped_and_preserved_per_provider() -> None:
    web = service()
    view = await web.configure("a", "one", ConfigureWebRequest(
        provider="tavily", apiKey=SecretStr("personal-secret")
    ))
    assert view.personal_key_configured
    assert "personal-secret" not in view.model_dump_json()
    stored = await web.credentials.repository.get("a", "one", REFERENCE)
    assert stored and "personal-secret" not in stored.ciphertext
    assert not (await web.get("a", "two")).personal_key_configured
    assert not (await web.get("b", "one")).personal_key_configured
    await web.configure("a", "one", ConfigureWebRequest(provider="platform"))
    view = await web.configure("a", "one", ConfigureWebRequest(provider="tavily"))
    assert view.personal_key_configured
    view = await web.configure("a", "one", ConfigureWebRequest(provider="tavily", clearKey=True))
    assert not view.credential_configured


@pytest.mark.asyncio
async def test_switch_persists_across_instances_and_blocks_active_client() -> None:
    web = service()
    other_worker = WebConfigurationService(web.credentials)
    identity = ExecutionIdentity(tenant_id="a", user_id="one", project_id="p",
        session_id="s", run_id="r", agent_name="agent", agent_version="1")
    client = UserWebClient(other_worker, identity)
    await web.configure("a", "one", ConfigureWebRequest(enabled=False))
    assert not (await other_worker.get("a", "one")).effective_enabled
    assert (await other_worker.get("a", "two")).effective_enabled
    with pytest.raises(WebAccessError, match="关闭联网"):
        await client.fetch("https://example.com")
    with pytest.raises(WebAccessError, match="关闭联网"):
        await client.search("test")
    await web.configure("a", "one", ConfigureWebRequest(enabled=True))
    assert (await other_worker.get("a", "one")).effective_enabled
    other_worker.enabled = False
    assert not (await other_worker.get("a", "one")).effective_enabled
