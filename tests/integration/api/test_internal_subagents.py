import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.api.test_agent_studio_api import SERVICE_TOKEN, app, draft_request


@pytest.mark.asyncio
async def test_internal_child_creation_editing_placement_and_conflicts() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-internal",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        parent = (
            await client.post("/v1/studio/drafts", headers=headers, json=draft_request("lead"))
        ).json()
        parent_id = parent["draftId"]
        payload = {
            "expectedRevision": 1,
            "displayName": "事实核验",
            "responsibility": "核验来源并返回证据和日期",
        }
        response = await client.post(
            f"/v1/studio/drafts/{parent_id}/subagents", headers=headers, json=payload
        )
        assert response.status_code == 201, response.text
        created = response.json()
        child = created["child"]
        child_id = child["draftId"]
        assert child["parentDraftId"] == parent_id
        assert child["spec"]["displayName"] == "事实核验"
        assert child["spec"]["skills"] == []
        assert created["parent"]["revision"] == 2
        assert created["parent"]["spec"]["subagents"][0]["ref"] == (
            f"{child['spec']['name']}@{child['spec']['version']}"
        )
        conflict = await client.post(
            f"/v1/studio/drafts/{parent_id}/subagents", headers=headers, json=payload
        )
        assert conflict.status_code == 409
        rows = (await client.get("/v1/studio/drafts", headers=headers)).json()
        assert len(rows) == 2  # A stale request cannot leave an orphan child.
        assert next(row for row in rows if row["draftId"] == child_id)["parentDraftId"] == parent_id
        nested = await client.post(
            f"/v1/studio/drafts/{child_id}/subagents", headers=headers, json=payload
        )
        assert nested.status_code == 409
        forbidden = await client.get(
            f"/v1/studio/drafts/{child_id}", headers={**headers, "X-User-ID": "other"}
        )
        assert forbidden.status_code == 404
        child["spec"]["systemPrompt"] += "\n必须标注证据日期。"
        saved = await client.put(
            f"/v1/studio/drafts/{child_id}",
            headers=headers,
            json={"expectedRevision": 1, "spec": child["spec"]},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["parentDraftId"] == parent_id
        published = await client.post(
            f"/v1/studio/drafts/{child_id}/publish", headers=headers, json={"expectedRevision": 2}
        )
        assert published.status_code == 200, published.text
        catalog = (await client.get("/v1/agents", headers=headers)).json()
        assert not any(item["name"] == child["spec"]["name"] for item in catalog)
        current = (await client.get(f"/v1/studio/drafts/{child_id}", headers=headers)).json()
        restored = await client.put(
            f"/v1/studio/drafts/{child_id}/placement",
            headers=headers,
            json={"expectedRevision": current["revision"], "parentDraftId": None},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["parentDraftId"] is None
        # Still referenced by its parent, but independently usable after promotion.
        catalog = (await client.get("/v1/agents", headers=headers)).json()
        assert any(item["name"] == child["spec"]["name"] for item in catalog)
        adopted = await client.put(
            f"/v1/studio/drafts/{child_id}/placement",
            headers=headers,
            json={"expectedRevision": restored.json()["revision"], "parentDraftId": parent_id},
        )
        assert adopted.status_code == 200, adopted.text
        deletion = await client.delete(
            f"/v1/studio/drafts/{parent_id}", headers=headers, params={"expectedRevision": 2}
        )
        assert deletion.status_code == 409
