import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_app
from harness.api.dependencies import build_memory_container


@pytest.mark.asyncio
async def test_sharing_studio_release_creates_shared_editable_draft() -> None:
    container = build_memory_container()
    owner_session = await container.auth.register(
        email="owner-auto-draft@example.com",
        password="Long-password-1",
        display_name="Owner",
    )
    admin_session = await container.auth.register(
        email="admin-auto-draft@example.com",
        password="Long-password-2",
        display_name="Admin",
    )
    owner_id = owner_session.user.user_id
    admin_id = admin_session.user.user_id
    app = create_app(container)
    token = container.api_bearer_token.get_secret_value()
    base = {"Authorization": f"Bearer {token}", "X-Tenant-ID": "local"}
    owner = {**base, "X-User-ID": owner_id}
    admin = {**base, "X-User-ID": admin_id}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        personal = await client.post(
            "/v1/studio/drafts",
            headers=owner,
            json={
                "name": "shared-researcher",
                "domain": "policy-research",
                "displayName": "共享研究助手",
                "description": "可在空间继续协作编辑的智能体。",
                "template": "analyst",
            },
        )
        published = await client.post(
            f"/v1/studio/drafts/{personal.json()['draftId']}/publish",
            headers=owner,
        )
        created_space = await client.post(
            "/v1/spaces", headers=owner, json={"name": "自动草稿协作组"}
        )
        space_id = created_space.json()["space"]["spaceId"]
        await client.put(
            f"/v1/spaces/{space_id}/members",
            headers=owner,
            json={"user_id": admin_id, "role": "admin"},
        )
        shared = await client.post(
            f"/v1/spaces/{space_id}/agents",
            headers=owner,
            json={
                "owner_user_id": owner_id,
                "name": published.json()["name"],
                "version": published.json()["version"],
            },
        )
        listed = await client.get(
            f"/v1/studio/drafts?spaceId={space_id}", headers=admin
        )
        editable = await client.get(
            f"/v1/studio/drafts/{listed.json()[0]['draftId']}", headers=admin
        )

    assert personal.status_code == 201
    assert published.status_code == 200
    assert shared.status_code == 201
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["agentId"] == shared.json()["release"]["agentId"]
    assert listed.json()[0]["spaceId"] == space_id
    assert editable.status_code == 200
    assert editable.json()["spec"] == personal.json()["spec"]
    assert editable.json()["publishedVersion"] == published.json()["version"]


@pytest.mark.asyncio
async def test_workspace_shared_draft_read_write_and_publish() -> None:
    container = build_memory_container()
    alice_session = await container.auth.register(
        email="alice-draft@example.com", password="Long-password-1", display_name="Alice"
    )
    bob_session = await container.auth.register(
        email="bob-draft@example.com", password="Long-password-2", display_name="Bob"
    )
    carol_session = await container.auth.register(
        email="carol-draft@example.com", password="Long-password-3", display_name="Carol"
    )
    dave_session = await container.auth.register(
        email="dave-draft@example.com", password="Long-password-4", display_name="Dave"
    )
    alice_id = alice_session.user.user_id
    bob_id = bob_session.user.user_id
    carol_id = carol_session.user.user_id
    dave_id = dave_session.user.user_id
    version = await container.agents.publish(
        "local", alice_id, "agents/lead-agent/agent.yaml", environment="production"
    )
    # The orchestrator template references helper-agent@1.0.0; dependency
    # validation resolves subagents in the publishing member's namespace.
    for owner_id in (alice_id, bob_id):
        await container.agents.publish(
            "local", owner_id, "agents/helper-agent/agent.yaml", environment="production"
        )
    app = create_app(container)
    token = container.api_bearer_token.get_secret_value()
    base = {"Authorization": f"Bearer {token}", "X-Tenant-ID": "local"}
    alice = {**base, "X-User-ID": alice_id}
    bob = {**base, "X-User-ID": bob_id}
    carol = {**base, "X-User-ID": carol_id}
    dave = {**base, "X-User-ID": dave_id}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/v1/spaces", headers=alice, json={"name": "草稿协作组"})
        space_id = created.json()["space"]["spaceId"]
        for user_id, role in ((bob_id, "contributor"), (carol_id, "viewer"), (dave_id, "viewer")):
            member = await client.put(
                f"/v1/spaces/{space_id}/members",
                headers=alice,
                json={"user_id": user_id, "role": role},
            )
            assert member.status_code == 200
        shared = await client.post(
            f"/v1/spaces/{space_id}/agents",
            headers=alice,
            json={
                "owner_user_id": alice_id,
                "name": version.name,
                "version": version.version,
            },
        )
        assert shared.status_code == 201
        agent_id = shared.json()["release"]["agentId"]

        # 1. Only members with EDIT can create the shared draft; the draft
        # name must match the workspace Agent identity.
        denied_create = await client.post(
            "/v1/studio/drafts",
            headers=carol,
            json={
                "name": version.name,
                "domain": "general-assistant",
                "display_name": "共享 Lead",
                "description": "shared draft",
                "template": "orchestrator",
                "agentId": agent_id,
                "spaceId": space_id,
            },
        )
        assert denied_create.status_code == 403
        wrong_name = await client.post(
            "/v1/studio/drafts",
            headers=alice,
            json={
                "name": "wrong-name",
                "domain": "general-assistant",
                "display_name": "共享 Lead",
                "description": "shared draft",
                "template": "orchestrator",
                "agentId": agent_id,
                "spaceId": space_id,
            },
        )
        assert wrong_name.status_code == 409
        created_draft = await client.post(
            "/v1/studio/drafts",
            headers=alice,
            json={
                "name": version.name,
                "domain": "general-assistant",
                "display_name": "共享 Lead",
                "description": "shared draft",
                "template": "orchestrator",
                "agentId": agent_id,
                "spaceId": space_id,
            },
        )
        assert created_draft.status_code == 201
        draft_id = created_draft.json()["draftId"]
        assert created_draft.json()["agentId"] == agent_id
        assert created_draft.json()["spaceId"] == space_id
        spec = created_draft.json()["spec"]

        # A second shared draft for the same Agent conflicts.
        duplicate = await client.post(
            "/v1/studio/drafts",
            headers=alice,
            json={
                "name": version.name,
                "domain": "general-assistant",
                "display_name": "重复",
                "description": "duplicate",
                "template": "orchestrator",
                "agentId": agent_id,
                "spaceId": space_id,
            },
        )
        assert duplicate.status_code == 409

        # 2. Contributors read and write the shared draft; viewers only read.
        fetched = await client.get(f"/v1/studio/drafts/{draft_id}", headers=bob)
        assert fetched.status_code == 200
        assert fetched.json()["revision"] == 1
        replaced = await client.put(
            f"/v1/studio/drafts/{draft_id}",
            headers=bob,
            json={"expectedRevision": 1, "spec": spec},
        )
        assert replaced.status_code == 200
        assert replaced.json()["revision"] == 2
        assert replaced.json()["updatedBy"] == bob_id
        carol_view = await client.get(f"/v1/studio/drafts/{draft_id}", headers=carol)
        assert carol_view.status_code == 200
        carol_write = await client.put(
            f"/v1/studio/drafts/{draft_id}",
            headers=carol,
            json={"expectedRevision": 2, "spec": replaced.json()["spec"]},
        )
        assert carol_write.status_code == 403

        # 3. Renaming the shared draft would change the Agent identity -> 409.
        renamed_spec = dict(replaced.json()["spec"])
        renamed_spec["name"] = "renamed-agent"
        rename = await client.put(
            f"/v1/studio/drafts/{draft_id}",
            headers=alice,
            json={"expectedRevision": 2, "spec": renamed_spec},
        )
        assert rename.status_code == 409

        # 4. ACL user grant lets the viewer edit; group grants apply to all
        # group members.
        acl = await client.put(
            f"/v1/spaces/{space_id}/agents/{agent_id}/acl",
            headers=alice,
            json={"grantee_type": "user", "grantee_id": carol_id, "permission": "edit"},
        )
        assert acl.status_code == 201
        carol_write = await client.put(
            f"/v1/studio/drafts/{draft_id}",
            headers=carol,
            json={"expectedRevision": 2, "spec": replaced.json()["spec"]},
        )
        assert carol_write.status_code == 200
        assert carol_write.json()["revision"] == 3
        group = await client.post(
            "/v1/groups", headers=alice, json={"name": "草稿组", "description": ""}
        )
        group_id = group.json()["groupId"]
        await client.put(f"/v1/groups/{group_id}/members", headers=alice, json={"user_id": dave_id})
        await client.put(
            f"/v1/spaces/{space_id}/agents/{agent_id}/acl",
            headers=alice,
            json={"grantee_type": "group", "grantee_id": group_id, "permission": "edit"},
        )
        dave_write = await client.put(
            f"/v1/studio/drafts/{draft_id}",
            headers=dave,
            json={"expectedRevision": 3, "spec": carol_write.json()["spec"]},
        )
        assert dave_write.status_code == 200
        assert dave_write.json()["revision"] == 4

        # 5. The workspace draft shows up in the space-scoped listing.
        listed = await client.get(f"/v1/studio/drafts?spaceId={space_id}", headers=carol)
        assert listed.status_code == 200
        assert [item["draftId"] for item in listed.json()] == [draft_id]

        # 6. Publishing the shared draft releases the version into the space
        # and promotes it as the current version.
        published = await client.post(
            f"/v1/studio/drafts/{draft_id}/publish",
            headers=bob,
            json={"expectedRevision": 4},
        )
        assert published.status_code == 200
        assert published.json()["agent_id"] == agent_id
        agents = await client.get(f"/v1/spaces/{space_id}/agents", headers=bob)
        item = next(entry for entry in agents.json() if entry["agent"]["agentId"] == agent_id)
        assert item["agent"]["currentVersion"] == published.json()["version"]
        catalog = await client.get("/v1/agents", headers=bob)
        team_items = [entry for entry in catalog.json() if entry["agent_id"] == agent_id]
        assert any(entry["version"] == published.json()["version"] for entry in team_items)

        # 7. A non-member cannot read the shared draft.
        outside = await container.auth.register(
            email="outsider-draft@example.com",
            password="Long-password-5",
            display_name="Outside",
        )
        outsider = {**base, "X-User-ID": outside.user.user_id}
        hidden = await client.get(f"/v1/studio/drafts/{draft_id}", headers=outsider)
        assert hidden.status_code == 404
