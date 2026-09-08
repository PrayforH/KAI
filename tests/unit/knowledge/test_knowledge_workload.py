from __future__ import annotations

import jwt
import pytest
from pydantic import SecretStr

from harness.core.models import ExecutionIdentity
from harness.knowledge.models import KnowledgeResultTrust, KnowledgeSnapshotBinding
from harness.knowledge.workload import KnowledgeWorkloadTokenService


def identity() -> ExecutionIdentity:
    return ExecutionIdentity(
        tenant_id="tenant-a",
        user_id="user-a",
        project_id="project-a",
        session_id="session-a",
        run_id="run-a",
        agent_name="policy-agent",
        agent_version="1.0.0",
    )


def binding() -> KnowledgeSnapshotBinding:
    return KnowledgeSnapshotBinding(
        knowledgeBaseReference="company-policy",
        sourceReference="handbook",
        snapshotId="snapshot-a",
        trust=KnowledgeResultTrust.SENSITIVE,
    )


def test_workload_token_round_trips_identity_and_pinned_bindings() -> None:
    service = KnowledgeWorkloadTokenService(SecretStr("k" * 64))

    verified_identity, bindings = service.verify(service.issue(identity(), (binding(),)))

    assert verified_identity == identity()
    assert bindings == (binding(),)


def test_workload_token_rejects_tampering() -> None:
    service = KnowledgeWorkloadTokenService(SecretStr("k" * 64))
    token = service.issue(identity(), (binding(),))
    prefix, payload, signature = token.split(".")
    tampered = ".".join((prefix, ("A" if payload[0] != "A" else "B") + payload[1:], signature))

    with pytest.raises(jwt.InvalidTokenError):
        service.verify(tampered)
