"""Run inside an API container. Tokens stay in memory and are revoked on exit.

Default: read-only authenticated smoke check. --exercise creates a labelled manual-only
objective and exercises its lifecycle. --run additionally executes the harmless smoke prompt.
"""

import argparse
import asyncio
import json
from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from harness.composition import build_production_container
from harness.config import Settings


async def main(exercise: bool, execute: bool) -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        owner = (
            await connection.execute(
                text(
                    "select m.tenant_id, m.user_id from tenant_memberships m join users u "
                    "on u.user_id=m.user_id where not u.disabled and m.role in ('owner','admin') "
                    "order by m.created_at limit 1"
                )
            )
        ).one()
    await engine.dispose()
    container = build_production_container(settings, execution_enabled=False)
    user, membership = await container.auth.profile(owner.tenant_id, owner.user_id)
    auth = await container.auth._issue_session(user, membership)
    report = {"checks": [], "execution": "not_requested"}
    objective = None
    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000",
            timeout=30,
            headers={"Authorization": f"Bearer {auth.access_token}"},
        ) as client:
            for path in (
                "/healthz",
                "/v1/auth/me",
                "/v1/studio/projects",
                "/v1/agui/threads",
                "/v1/studio/models",
                "/v1/studio/automations",
                "/v1/studio/assistant/objectives",
                "/v1/studio/assistant/inbox",
                "/v1/studio/assistant/feishu/status",
            ):
                response = await client.get(path)
                assert response.status_code == 200, (path, response.status_code)
                report["checks"].append(path)
            connection = (await client.get("/v1/studio/assistant/feishu/status")).json()
            report["feishu"] = {key: connection[key] for key in ("configured", "enabled", "receiverOnline", "bound")}
            if exercise:
                response = await client.get("/v1/agents")
                response.raise_for_status()
                personal = [a for a in response.json() if a.get("scope") == "personal"]
                agent = next((a for a in personal if a.get("platform_default")), personal[0])
                response = await client.post(
                    "/v1/studio/assistant/objectives",
                    json={
                        "title": "KAI 助理上线验收（手动事项）",
                        "instructions": "这是一条 KAI 持续事项上线验收。请仅回复 KAI_ASSISTANT_OK，"
                        "不调用任何工具，不访问用户资料。",
                        "agentName": agent["name"],
                        "agentVersion": agent["version"],
                        "schedule": None,
                        "notificationMode": "none",
                    },
                )
                assert response.status_code == 201, ("create", response.status_code)
                objective = response.json()
                prefix = "/v1/studio/assistant/objectives/" + objective["objectiveId"]
                for action in ("pause", "resume"):
                    response = await client.post(
                        prefix + "/actions",
                        json={"action": action, "expectedRevision": objective["revision"]},
                    )
                    assert response.status_code == 200, (action, response.status_code)
                    objective = response.json()
                response = await client.post(
                    prefix + "/notes",
                    json={
                        "content": "验收仅检查文字回复。",
                        "expectedRevision": objective["revision"],
                    },
                )
                assert response.status_code == 200, ("note", response.status_code)
                objective = response.json()
                stale = await client.post(
                    prefix + "/actions", json={"action": "pause", "expectedRevision": 1}
                )
                assert stale.status_code == 409
                key = uuid4().hex
                first = await client.post(prefix + "/executions", headers={"Idempotency-Key": key})
                second = await client.post(prefix + "/executions", headers={"Idempotency-Key": key})
                assert first.status_code == second.status_code == 202
                assert first.json()["executionId"] == second.json()["executionId"]
                report["objectiveId"] = objective["objectiveId"]
                report["executionId"] = first.json()["executionId"]
                report["checks"].extend(
                    ["create", "pause", "resume", "note", "revision_conflict", "enqueue_dedup"]
                )
                if execute:
                    for _ in range(60):
                        result = await client.get(prefix + "/executions")
                        result.raise_for_status()
                        record = result.json()[0]
                        if record["status"] not in {"pending", "running"}:
                            report["execution"] = record["status"]
                            report["runId"] = record["runId"]
                            assert record["status"] == "succeeded", (
                                "execution",
                                record["status"],
                                record.get("errorCode"),
                            )
                            assert "KAI_ASSISTANT_OK" in record["summary"], (
                                "unexpected smoke response"
                            )
                            notices = (await client.get("/v1/studio/assistant/inbox")).json()
                            assert any(n["executionId"] == record["executionId"] for n in notices)
                            break
                        await asyncio.sleep(3)
                    else:
                        raise TimeoutError("assistant smoke run did not finish")
                response = await client.post(
                    prefix + "/actions",
                    json={
                        "action": "complete" if execute else "stop",
                        "expectedRevision": objective["revision"],
                    },
                )
                assert response.status_code == 200
                report["finalObjectiveStatus"] = response.json()["status"]
                objective = None
    finally:
        if objective is not None and container.assistant is not None:
            from datetime import UTC, datetime

            from harness.assistant.models import ObjectiveAction

            await container.assistant.repository.update(
                owner.tenant_id,
                owner.user_id,
                objective["objectiveId"],
                expected_revision=objective["revision"],
                now=datetime.now(UTC),
                action=ObjectiveAction.STOP,
            )
        await container.auth.logout(auth.refresh_token)
        if container.close:
            await container.close()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exercise", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.exercise or args.run, args.run))
