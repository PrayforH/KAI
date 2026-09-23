import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.api.dependencies import Identity, require_identity


@pytest.mark.asyncio
async def test_knowledge_api_file_sync_search_and_citations() -> None:
    app = create_memory_app()
    app.dependency_overrides[require_identity] = lambda: Identity(
        tenant_id="tenant-a",
        user_id="owner-a",
        roles=frozenset({"owner"}),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        created_source = await client.post(
            "/v1/studio/knowledge/sources",
            json={
                "reference": "employee-handbook",
                "displayName": "员工手册",
                "kind": "file",
                "config": {
                    "type": "file",
                    "documents": [
                        {
                            "documentId": "leave",
                            "title": "休假制度",
                            "content": "正式员工每年享有十五天带薪年假。",
                            "sourceUri": "knowledge://handbook/leave",
                        }
                    ],
                },
            },
        )
        created_base = await client.post(
            "/v1/studio/knowledge/bases",
            json={
                "reference": "company-policy",
                "displayName": "公司制度",
                "sourceReferences": ["employee-handbook"],
            },
        )
        searched = await client.post(
            "/v1/studio/knowledge/search",
            json={
                "query": "年假有多少天",
                "knowledgeBaseReferences": ["company-policy"],
            },
        )
        snapshots = await client.get("/v1/studio/knowledge/snapshots")
        hit_payload = searched.json()["hits"][0]
        citation = hit_payload["citation"]
        opened = await client.get(
            f"/v1/studio/knowledge/citations/{citation['snapshotId']}/{citation['chunkId']}"
        )

    assert created_source.status_code == 201, created_source.text
    source_payload = created_source.json()
    assert source_payload["source"]["health"] == "healthy"
    assert "config" not in source_payload["source"]
    assert "acl" not in source_payload["source"]
    assert source_payload["sync"]["status"] == "succeeded"
    assert created_base.status_code == 201, created_base.text
    assert searched.status_code == 200, searched.text
    hit = hit_payload
    assert "十五天" in hit["content"]
    assert hit["trust"] == "sensitive"
    assert hit["citation"] == {
        "knowledgeBaseReference": "company-policy",
        "sourceReference": "employee-handbook",
        "sourceDisplayName": "员工手册",
        "snapshotId": source_payload["source"]["activeSnapshotId"],
        "documentId": "leave",
        "chunkId": hit["citation"]["chunkId"],
        "title": "休假制度",
        "uri": "knowledge://handbook/leave",
    }
    assert snapshots.status_code == 200
    assert len(snapshots.json()) == 1
    assert opened.status_code == 200
    assert "十五天" in opened.text
    assert opened.headers["content-disposition"].startswith("inline;")


@pytest.mark.asyncio
async def test_knowledge_source_management_requires_writer() -> None:
    app = create_memory_app()
    app.dependency_overrides[require_identity] = lambda: Identity(
        tenant_id="tenant-a",
        user_id="viewer-a",
        roles=frozenset({"viewer"}),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/studio/knowledge/sources",
            json={
                "reference": "blocked",
                "displayName": "Blocked",
                "kind": "file",
                "config": {
                    "type": "file",
                    "documents": [
                        {
                            "documentId": "one",
                            "title": "One",
                            "content": "Not created",
                        }
                    ],
                },
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_knowledge_api_hides_another_users_resources() -> None:
    app = create_memory_app()
    current_user = {"value": "owner-a"}
    app.dependency_overrides[require_identity] = lambda: Identity(
        tenant_id="tenant-a",
        user_id=current_user["value"],
        roles=frozenset({"owner"}),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        source = await client.post(
            "/v1/studio/knowledge/sources",
            json={
                "reference": "private-source",
                "displayName": "Private source",
                "kind": "file",
                "config": {
                    "type": "file",
                    "documents": [
                        {
                            "documentId": "private-document",
                            "title": "Private",
                            "content": "Only the owner can search this.",
                        }
                    ],
                },
            },
        )
        base = await client.post(
            "/v1/studio/knowledge/bases",
            json={
                "reference": "private-base",
                "displayName": "Private base",
                "sourceReferences": ["private-source"],
            },
        )
        current_user["value"] = "other-user"
        bases = await client.get("/v1/studio/knowledge/bases")
        sources = await client.get("/v1/studio/knowledge/sources")
        snapshots = await client.get("/v1/studio/knowledge/snapshots")
        hidden = await client.get("/v1/studio/knowledge/bases/private-base")
        searched = await client.post(
            "/v1/studio/knowledge/search",
            json={
                "query": "owner",
                "knowledgeBaseReferences": ["private-base"],
            },
        )

    assert source.status_code == 201
    assert base.status_code == 201
    assert bases.json() == []
    assert sources.json() == []
    assert snapshots.json() == []
    assert hidden.status_code == 404
    assert searched.status_code == 200
    assert searched.json()["hits"] == []


@pytest.mark.asyncio
async def test_create_engine_base_reports_missing_configuration_without_500() -> None:
    app = create_memory_app()
    app.dependency_overrides[require_identity] = lambda: Identity(
        tenant_id="tenant-a", user_id="owner-a", roles=frozenset({"owner"}),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/studio/knowledge/bases", json={
            "reference": "engine-check", "displayName": "Engine check", "engine": "weknora",
        })
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "knowledge_engine_not_configured"
