import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from harness.auth.audit import AuditService
from harness.auth.repositories import InMemoryAuditRepository
from harness.core.models import ExecutionIdentity
from harness.runtime.mcp_credentials import EmptyMcpCredentialProvider, McpCredentialError
from harness.studio.mcp_credential_store import (
    ConfigureMcpCredentialRequest,
    InMemoryMcpCredentialRepository,
    McpCredentialCipher,
    McpCredentialService,
    StoredMcpCredential,
    StoredMcpCredentialProvider,
)


def test_cryptography_vex_is_limited_to_the_unreachable_pkcs7_path() -> None:
    source_imports: set[str] = set()
    source_text = ""
    for path in Path("src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        source_text += text
        for line in text.splitlines():
            if line.startswith(("import cryptography", "from cryptography")):
                source_imports.add(line)

    assert source_imports == {
        "from cryptography.exceptions import InvalidTag",
        "from cryptography.hazmat.primitives.ciphers.aead import AESGCM",
    }
    assert "pkcs7" not in source_text.lower()

    vex = json.loads(
        Path("security/vex/cryptography-49.0.0.openvex.json").read_text(
            encoding="utf-8"
        )
    )
    statement = vex["statements"][0]
    assert statement["vulnerability"]["name"] == "CVE-2026-69247"
    assert statement["products"] == [{"@id": "pkg:pypi/cryptography@49.0.0"}]
    assert statement["status"] == "not_affected"
    assert statement["justification"] == "vulnerable_code_not_in_execute_path"


def identity(tenant_id: str = "tenant-a") -> ExecutionIdentity:
    return ExecutionIdentity(
        tenant_id=tenant_id,
        user_id="owner-a",
        project_id="project-a",
        session_id="session-a",
        run_id="run-a",
        agent_name="agent-a",
        agent_version="1",
    )


@pytest.mark.asyncio
async def test_credentials_are_encrypted_user_scoped_and_never_audited() -> None:
    repository = InMemoryMcpCredentialRepository()
    audit_repository = InMemoryAuditRepository()
    service = McpCredentialService(
        repository,
        McpCredentialCipher(SecretStr("encryption-key-for-tests")),
        audit=AuditService(audit_repository),
    )
    secret = "top-secret-mcp-token"

    status = await service.configure(
        "tenant-a",
        "owner-a",
        "company-search",
        ConfigureMcpCredentialRequest(authKey="authorization", value=SecretStr(secret)),
    )
    stored = await repository.get("tenant-a", "owner-a", "company-search")
    provider = StoredMcpCredentialProvider(service, EmptyMcpCredentialProvider())
    resolved = await provider.resolve("company-search", identity(), frozenset({"authorization"}))

    assert stored is not None
    assert secret not in stored.ciphertext
    serialized = status.model_dump(mode="json", by_alias=True)
    assert serialized["reference"] == "company-search"
    assert serialized["configured"] is True
    assert serialized["keyNames"] == ["authorization"]
    assert serialized["revision"] == 1
    assert serialized["updatedBy"] == "owner-a"
    assert serialized["updatedAt"] is not None
    assert secret not in json.dumps(serialized)
    assert resolved["authorization"].get_secret_value() == secret
    assert secret not in json.dumps(
        [entry.model_dump(mode="json") for entry in audit_repository.entries]
    )
    with pytest.raises(McpCredentialError, match="missing MCP credentials"):
        await provider.resolve("company-search", identity("tenant-b"), frozenset({"authorization"}))
    other_user = identity().model_copy(update={"user_id": "owner-b"})
    with pytest.raises(McpCredentialError, match="missing MCP credentials"):
        await provider.resolve("company-search", other_user, frozenset({"authorization"}))

    assert await service.delete("tenant-a", "owner-a", "company-search") is True
    assert await repository.get("tenant-a", "owner-a", "company-search") is None


@pytest.mark.asyncio
async def test_service_owned_mode_resolves_space_credentials_without_leaking_personal() -> None:
    repository = InMemoryMcpCredentialRepository()
    service = McpCredentialService(
        repository,
        McpCredentialCipher(SecretStr("encryption-key-for-tests")),
    )
    provider = StoredMcpCredentialProvider(service, EmptyMcpCredentialProvider())
    # The running user has personal credentials that must NOT leak into a
    # service_owned shared run.
    await service.configure(
        "tenant-a",
        "member-a",
        "company-search",
        ConfigureMcpCredentialRequest(authKey="authorization", value=SecretStr("personal-token")),
    )
    # The space provides the shared credential under its own owner identity.
    await service.configure(
        "tenant-a",
        "space:space-1",
        "company-search",
        ConfigureMcpCredentialRequest(authKey="authorization", value=SecretStr("shared-token")),
    )
    service_owned = ExecutionIdentity(
        tenant_id="tenant-a",
        user_id="member-a",
        team_ids=("space-1",),
        project_id="agent-a",
        session_id="session-a",
        run_id="run-a",
        agent_name="agent-a",
        agent_version="1",
        connection_mode="service_owned",
    )
    resolved = await provider.resolve(
        "company-search", service_owned, frozenset({"authorization"})
    )
    assert resolved["authorization"].get_secret_value() == "shared-token"

    # A caller_owned identity keeps resolving the caller's personal store.
    caller_owned = service_owned.model_copy(update={"connection_mode": "caller_owned"})
    resolved = await provider.resolve(
        "company-search", caller_owned, frozenset({"authorization"})
    )
    assert resolved["authorization"].get_secret_value() == "personal-token"

    # Removing the space credential makes service_owned runs fail closed even
    # though the caller still has personal credentials.
    await service.delete("tenant-a", "space:space-1", "company-search")
    with pytest.raises(McpCredentialError, match="missing MCP credentials"):
        await provider.resolve(
            "company-search", service_owned, frozenset({"authorization"})
        )


def test_cipher_can_read_pre_isolation_tenant_scoped_ciphertext() -> None:
    secret = "encryption-key-for-tests"
    cipher = McpCredentialCipher(SecretStr(secret))
    nonce = b"0" * 12
    plaintext = b'{"authorization":"legacy-token"}'
    key = hashlib.sha256(b"harness-mcp-v1\0" + secret.encode()).digest()
    sealed = AESGCM(key).encrypt(nonce, plaintext, b"tenant-a\0company-search")
    stored = StoredMcpCredential(
        tenant_id="tenant-a",
        owner_user_id="owner-a",
        reference="company-search",
        revision=1,
        key_names=("authorization",),
        ciphertext=base64.urlsafe_b64encode(nonce + sealed).decode(),
        updated_by="owner-a",
        updated_at=datetime.now(UTC),
    )

    assert cipher.decrypt(stored)["authorization"].get_secret_value() == "legacy-token"
