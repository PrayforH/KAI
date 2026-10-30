"""创建子助理的请求契约：身份字段由会话推导，客户端伪造即被拒。"""

import pytest
from pydantic import ValidationError

from harness.assistant.api import SpecialistCreateRequest


def test_request_accepts_duty_and_agent_only() -> None:
    body = SpecialistCreateRequest.model_validate(
        {
            "name": "费用监控",
            "description": "长期关注账号花费",
            "agentName": "audit-agent",
            "agentVersion": "1.0.0",
        }
    )
    assert body.name == "费用监控" and body.agent_name == "audit-agent"


@pytest.mark.parametrize(
    "identity",
    [
        {"tenantId": "other-tenant"},
        {"userId": "other-user"},
        {"specialistId": "forged"},
        {"status": "active"},
        {"config": {"credential": "x"}},
    ],
)
def test_request_rejects_client_supplied_identity_or_state(identity: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        SpecialistCreateRequest.model_validate(
            {
                "name": "费用监控",
                "agentName": "audit-agent",
                "agentVersion": "1.0.0",
                **identity,
            }
        )
