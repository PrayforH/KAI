import json
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from harness.studio.api import get_model_configuration_service
from tests.integration.api.test_agent_studio_api import SERVICE_TOKEN, app, draft_request


@pytest.mark.asyncio
async def test_multi_turn_preview_apply_conflict_and_scope() -> None:
    application = app()
    model = AsyncMock()
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (
            await client.post("/v1/studio/drafts", headers=headers, json=draft_request())
        ).json()
        path = f"/v1/studio/drafts/{draft['draftId']}"
        original = draft["spec"]
        model.complete_text.return_value = json.dumps(
            {
                "reply": "将结果改为表格，等待确认。",
                "changes": {"systemPrompt": original["systemPrompt"] + "\n输出表格。"},
            }
        )
        messages = [{"role": "user", "content": "输出改为表格"}]
        preview = await client.post(
            path + "/builder-conversation",
            headers=headers,
            json={"expectedRevision": 1, "messages": messages},
        )
        assert preview.status_code == 200, preview.text
        proposal = preview.json()
        assert proposal["changedFields"] == ["systemPrompt"]
        assert list(proposal["changes"]) == ["systemPrompt"]  # No null defaults on the wire.
        assert (await client.get(path, headers=headers)).json() == draft
        comparison_response = await client.post(
            path + "/builder-project-diff",
            headers=headers,
            json={"expectedRevision": 1, "changes": proposal["changes"]},
        )
        assert comparison_response.status_code == 200, comparison_response.text
        comparison = comparison_response.json()
        assert comparison["before"]["revision"] == 1
        assert comparison["after"]["revision"] == 2
        assert (await client.get(path, headers=headers)).json() == draft
        old_files = {f["path"]: f for f in comparison["before"]["files"]}
        new_files = {f["path"]: f for f in comparison["after"]["files"]}
        prompt_path = "src/sapling_deep_agents/prompts/system.md"
        assert old_files[prompt_path]["digest"] != new_files[prompt_path]["digest"]
        assert "输出表格" in new_files["src/sapling_deep_agents/prompts/system.md"]["content"]
        forbidden_diff = await client.post(
            path + "/builder-project-diff",
            headers={**headers, "X-User-ID": "other"},
            json={"expectedRevision": 1, "changes": proposal["changes"]},
        )
        assert forbidden_diff.status_code == 404
        applied = await client.post(
            path + "/builder-apply",
            headers=headers,
            json={"expectedRevision": 1, "changes": proposal["changes"]},
        )
        assert applied.status_code == 200, applied.text
        saved = applied.json()
        assert saved["draftId"] == draft["draftId"]
        assert saved["revision"] == 2
        assert saved["publishedVersion"] is None
        assert saved["spec"]["skills"] == original["skills"]
        assert saved["spec"]["model"] == original["model"]
        actual = (
            await client.get(
                path + "/deepagents-project/files", headers=headers, params={"expectedRevision": 2}
            )
        ).json()
        assert actual["files"] == comparison["after"]["files"]
        stale_diff = await client.post(
            path + "/builder-project-diff",
            headers=headers,
            json={"expectedRevision": 1, "changes": proposal["changes"]},
        )
        assert stale_diff.status_code == 409
        stale = await client.post(
            path + "/builder-apply",
            headers=headers,
            json={"expectedRevision": 1, "changes": proposal["changes"]},
        )
        assert stale.status_code == 409
        messages += [
            {"role": "assistant", "content": "已改为表格"},
            {"role": "user", "content": "再简短一点，最多三行"},
        ]
        model.complete_text.return_value = json.dumps(
            {
                "reply": "压缩为三行表格。",
                "changes": {"systemPrompt": saved["spec"]["systemPrompt"] + "\n最多三行。"},
            }
        )
        second = await client.post(
            path + "/builder-conversation",
            headers=headers,
            json={"expectedRevision": 2, "messages": messages},
        )
        assert second.status_code == 200, second.text
        model_context = json.loads(model.complete_text.call_args.kwargs["user_prompt"])
        assert model_context["currentDraft"]["systemPrompt"] == saved["spec"]["systemPrompt"]
        assert len(model_context["conversation"]) == 3
        second_saved = await client.post(
            path + "/builder-apply",
            headers=headers,
            json={"expectedRevision": 2, "changes": second.json()["changes"]},
        )
        assert second_saved.status_code == 200
        assert second_saved.json()["revision"] == 3
        assert "输出表格" in second_saved.json()["spec"]["systemPrompt"]
        assert "最多三行" in second_saved.json()["spec"]["systemPrompt"]
        assert len((await client.get("/v1/studio/drafts", headers=headers)).json()) == 1
        forbidden = await client.post(
            path + "/builder-conversation",
            headers={**headers, "X-User-ID": "other"},
            json={"expectedRevision": 3, "messages": messages},
        )
        assert forbidden.status_code == 404
        for changes, status in [
            ({"executionProfile": "local"}, 409),
            ({"systemPrompt": None}, 422),
            ({"builtinTools": ["NotARegisteredTool"]}, 409),
            ({"mcpServers": ["new-external-server"]}, 409),
            ({"knowledgeReferences": ["tavily-readonly"]}, 409),
            ({"skillInstructions": [{"name": "missing", "instructions": "changed"}]}, 409),
            ({"roleResponsibilities": [{"alias": "missing", "responsibility": "changed"}]}, 409),
        ]:
            response = await client.post(
                path + "/builder-apply",
                headers=headers,
                json={"expectedRevision": 3, "changes": changes},
            )
            assert response.status_code == status, response.text
        assert (await client.get(path, headers=headers)).json()["revision"] == 3
        model.complete_text.return_value = "not json"
        invalid = await client.post(
            path + "/builder-conversation",
            headers=headers,
            json={"expectedRevision": 3, "messages": messages},
        )
        assert invalid.status_code == 409
        model.complete_text.return_value = '{"reply":"请说明输出格式","changes":{}}'
        clarified = await client.post(
            path + "/builder-conversation",
            headers=headers,
            json={"expectedRevision": 3, "messages": messages},
        )
        assert clarified.json()["changedFields"] == []


@pytest.mark.asyncio
async def test_edit_during_generation_invalidates_proposal() -> None:
    application = app()
    model = AsyncMock()
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (
            await client.post("/v1/studio/drafts", headers=headers, json=draft_request())
        ).json()
        path = f"/v1/studio/drafts/{draft['draftId']}"

        async def meanwhile(*args: object, **kwargs: object) -> str:
            draft["spec"]["description"] = "手动编辑的内容"
            response = await client.put(
                path, headers=headers, json={"expectedRevision": 1, "spec": draft["spec"]}
            )
            assert response.status_code == 200
            return '{"reply":"已准备建议","changes":{"description":"模型的新建议"}}'

        model.complete_text.side_effect = meanwhile
        response = await client.post(
            path + "/builder-conversation",
            headers=headers,
            json={"expectedRevision": 1, "messages": [{"role": "user", "content": "修改简介"}]},
        )
        assert response.status_code == 409
        assert (await client.get(path, headers=headers)).json()["spec"][
            "description"
        ] == "手动编辑的内容"


@pytest.mark.asyncio
async def test_auto_intents_are_read_only_structured_and_keep_clarification_context() -> None:
    application = app()
    model = AsyncMock()
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (
            await client.post("/v1/studio/drafts", headers=headers, json=draft_request())
        ).json()
        path = f"/v1/studio/drafts/{draft['draftId']}"
        messages = [{"role": "user", "content": "再短一点"}]
        for action, task, text in [
            ("ask", "", "是修改默认行为还是只改本次结果？"),
            ("run", "根据原始材料输出三行摘要", "测试三行摘要"),
            ("rerun", "", "重新执行上次任务"),
            ("reply", "", "当前配置使用结构化输出"),
        ]:
            model.complete_text.return_value = json.dumps(
                {
                    "reply": text,
                    "action": action,
                    "task": task,
                    "changes": {},
                }
            )
            response = await client.post(
                path + "/builder-conversation",
                headers=headers,
                json={
                    "intent": "auto",
                    "expectedRevision": 1,
                    "messages": messages,
                    "runContext": '{"task":"原始材料摘要","status":"succeeded"}',
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["action"] == action
            assert response.json()["task"] == task
            assert response.json()["changedFields"] == []
            assert (await client.get(path, headers=headers)).json() == draft
            context = json.loads(model.complete_text.call_args.kwargs["user_prompt"])
            assert context["conversation"] == messages
            assert "自动意图判断" in model.complete_text.call_args.kwargs["system_prompt"]
            messages.extend(
                [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "只改本次结果"},
                ]
            )

        for payload in [
            {"reply": "开始", "action": "run", "changes": {}},  # Missing executable task.
            {"reply": "开始", "action": "run", "task": "测试", "changes": {"description": "偷改"}},
            {"reply": "请说明", "action": "ask", "changes": {"description": "偷改"}},
            {"reply": "已发布", "action": "publish", "changes": {}},
            {"reply": "未指定意图", "changes": {}},
        ]:
            model.complete_text.return_value = json.dumps(payload)
            response = await client.post(
                path + "/builder-conversation",
                headers=headers,
                json={
                    "intent": "auto",
                    "expectedRevision": 1,
                    "messages": messages,
                },
            )
            assert response.status_code == 409, response.text
        assert (await client.get(path, headers=headers)).json() == draft

        model.complete_text.return_value = json.dumps(
            {
                "reply": "测试",
                "action": "run",
                "task": "测试",
                "changes": {},
            }
        )
        response = await client.post(
            path + "/builder-conversation",
            headers=headers,
            json={
                "intent": "edit",
                "expectedRevision": 1,
                "messages": messages,
            },
        )
        assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_skill_creator_and_catalog_assembly_share_review_and_atomic_apply(
    streaming: bool,
) -> None:
    from unittest.mock import patch

    from harness.studio.models import DraftSkill, DraftSkillFile
    from harness.studio.skill_builder import SkillConversationReply

    application = app()
    model = AsyncMock()
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (
            await client.post("/v1/studio/drafts", headers=headers, json=draft_request())
        ).json()
        path = f"/v1/studio/drafts/{draft['draftId']}"
        model.complete_text.return_value = '{"reply":"目录说明","changes":{}}'
        request = {"expectedRevision": 1, "messages": [{"role": "user", "content": "查看能力"}]}
        await client.post(path + "/builder-conversation", headers=headers, json=request)
        context = json.loads(model.complete_text.call_args.kwargs["user_prompt"])
        catalog = context["assemblyCatalog"]
        assert "endpointUrl" not in str(catalog)
        assert "customHeaders" not in str(catalog)
        changes = {
            "builtinTools": [*draft["spec"]["builtinTools"], "WebFetch"],
            "capabilityCatalogRevision": catalog["revision"],
        }
        changes["builtinTools"] = list(dict.fromkeys(changes["builtinTools"]))
        model.complete_text.return_value = json.dumps(
            {
                "reply": "创建技能并装配联网",
                "changes": {
                    **{k: v for k, v in changes.items() if k != "capabilityCatalogRevision"},
                    "skillRequests": [
                        {
                            "operation": "create",
                            "name": "source-review",
                            "request": "创建公开资料审阅技能，附一个来源格式模板",
                        }
                    ],
                },
            }
        )
        generated = DraftSkill(
            name="source-review",
            description="审阅公开资料时使用",
            instructions="查验来源并按 assets/template.md 整理。",
            files=(DraftSkillFile(path="assets/template.md", content="来源 | 结论\n"),),
        )
        with patch(
            "harness.studio.worker_skill_creator.WorkerSkillCreator.respond", new_callable=AsyncMock
        ) as creator:
            creator.return_value = SkillConversationReply(
                status="ready", reply="待审阅", skill=generated
            )
            preview = await client.post(
                path + "/builder-conversation",
                headers={**headers, **({"Accept": "text/event-stream"} if streaming else {})},
                json=request,
            )
        assert preview.status_code == 200, preview.text
        assert creator.call_args.args[1].context.current_skill is None
        if streaming:
            emitted = [
                json.loads(line[6:])
                for line in preview.text.splitlines()
                if line.startswith("data: ")
            ]
            assert emitted[-1]["type"] == "result", emitted
            plan = emitted[-1]["result"]["changes"]
        else:
            plan = preview.json()["changes"]
        assert plan["createSkills"][0]["files"][0]["path"] == "assets/template.md"
        assert (await client.get(path, headers=headers)).json()["revision"] == 1
        body = {"expectedRevision": 1, "changes": plan}
        diff = await client.post(path + "/builder-project-diff", headers=headers, json=body)
        assert diff.status_code == 200, diff.text
        assert any(
            f["path"].endswith("source-review/assets/template.md")
            for f in diff.json()["after"]["files"]
        )
        for endpoint in ["builder-project-diff", "builder-apply"]:
            # "private-invisible" is not in the catalog and "tavily-readonly" was
            # retired platform-wide: assembly refuses both instead of dropping
            # the capability silently.
            for refused in ["private-invisible", "tavily-readonly"]:
                invalid = {**plan, "mcpServers": [refused]}
                denied = await client.post(
                    path + "/" + endpoint,
                    headers=headers,
                    json={"expectedRevision": 1, "changes": invalid},
                )
                assert denied.status_code == 409, refused
            stale = await client.post(
                path + "/" + endpoint,
                headers=headers,
                json={
                    "expectedRevision": 1,
                    "changes": {**plan, "capabilityCatalogRevision": catalog["revision"] + 1},
                },
            )
            assert stale.status_code == 409
        applied = await client.post(path + "/builder-apply", headers=headers, json=body)
        assert applied.status_code == 200, applied.text
        assert applied.json()["revision"] == 2
        source = await client.get(
            path + "/deepagents-project/files", headers=headers, params={"expectedRevision": 2}
        )
        assert source.status_code == 200, source.text
        assert source.json()["files"] == diff.json()["after"]["files"]
        # Same named create never silently overwrites; updates are explicit and reviewable.
        conflict = await client.post(
            path + "/builder-apply",
            headers=headers,
            json={"expectedRevision": 2, "changes": {"createSkills": plan["createSkills"]}},
        )
        assert conflict.status_code == 409
        update = {**plan["createSkills"][0], "instructions": "核验来源、作者与发布日期。"}
        updated = await client.post(
            path + "/builder-apply",
            headers=headers,
            json={"expectedRevision": 2, "changes": {"updateSkills": [update]}},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["spec"]["skills"][-1]["files"][0]["content"] == "来源 | 结论\n"
        secret = {**update, "files": [{"path": ".env", "content": "API_KEY=private"}]}
        rejected = await client.post(
            path + "/builder-apply",
            headers=headers,
            json={"expectedRevision": 3, "changes": {"updateSkills": [secret]}},
        )
        assert rejected.status_code == 422
        assert (await client.get(path, headers=headers)).json()["revision"] == 3


@pytest.mark.asyncio
async def test_streaming_builder_previews_without_applying_and_reports_invalid_reply() -> None:
    application = app()
    model = AsyncMock()
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
        "Accept": "text/event-stream",
    }

    async def complete(*args, **kwargs):
        chunks = ['{"reply":"流式', '建议", "changes":{}}']
        for chunk in chunks:
            await kwargs["on_delta"](chunk)
        return "".join(chunks)

    model.complete_text.side_effect = complete
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (
            await client.post("/v1/studio/drafts", headers=headers, json=draft_request())
        ).json()
        path = f"/v1/studio/drafts/{draft['draftId']}"
        body = {"expectedRevision": 1, "messages": [{"role": "user", "content": "解释配置"}]}
        response = await client.post(path + "/builder-conversation", headers=headers, json=body)
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert [e["text"] for e in events if e["type"] == "builder.reply"] == ["流式", "流式建议"]
        assert events[-1]["type"] == "result"
        assert events[-1]["result"]["changes"] == {}
        assert (await client.get(path, headers=headers)).json() == draft
        denied = await client.post(
            path + "/builder-conversation", headers={**headers, "X-User-ID": "other"}, json=body
        )
        assert denied.status_code == 404
        model.complete_text.side_effect = None
        model.complete_text.return_value = "invalid"
        invalid = await client.post(path + "/builder-conversation", headers=headers, json=body)
        assert '"type": "error"' in invalid.text
        assert '"type": "result"' not in invalid.text
        assert (await client.get(path, headers=headers)).json() == draft


def test_partial_builder_reply_handles_unicode_escapes_and_never_exposes_changes() -> None:
    from harness.studio.builder_conversation import partial_builder_reply

    assert partial_builder_reply('{"reply":"hello\\u4') == "hello"
    assert partial_builder_reply('{"reply":"hello\\u4f60') == "hello你"
    assert partial_builder_reply('{"reply":"ok", "changes":{"systemPrompt":"private') == "ok"


@pytest.mark.asyncio
async def test_task_creation_streams_real_stages_and_saves_one_draft() -> None:
    application = app()
    from tests.integration.api.test_agent_studio_api import INITIAL_AGENT_RESPONSE

    model = AsyncMock()
    model.complete_text.return_value = INITIAL_AGENT_RESPONSE
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
        "Accept": "text/event-stream",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/studio/drafts/from-task",
            headers=headers,
            json={"task": "汇总用户上传材料", "runtimePreference": "auto"},
        )
        assert response.status_code == 200
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert [e["type"] for e in events] == ["progress", "progress", "progress", "result"]
        assert events[-1]["result"]["recommendation"]["generatedByModel"] is True
        saved = events[-1]["result"]["draft"]
        drafts = (await client.get("/v1/studio/drafts", headers=headers)).json()
        assert [d["draftId"] for d in drafts] == [saved["draftId"]]


@pytest.mark.asyncio
async def test_eval_snapshot_stream_enforces_ownership_and_closes_on_completion() -> None:
    from tests.integration.api.test_agent_studio_api import app_and_container
    from tests.unit.evals.test_control_plane import drain, seed

    application, container = app_and_container()
    eval_id = await seed(container, "stream")
    await drain(container, container.eval_controller, eval_id)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get(f"/v1/studio/eval-runs/{eval_id}/events", headers=headers)
        assert response.status_code == 200
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert [e["type"] for e in events] == ["eval.snapshot", "result"]
        assert events[-1]["result"]["run"]["status"] in {"passed", "failed"}
        denied = await client.get(
            f"/v1/studio/eval-runs/{eval_id}/events", headers={**headers, "X-User-ID": "other"}
        )
        assert denied.status_code == 404


@pytest.mark.asyncio
async def test_builder_binds_visible_knowledge_and_web_with_apply_revalidation() -> None:
    application = app()
    model = AsyncMock()
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}",
               "X-Tenant-ID": "tenant-a", "X-User-ID": "builder-a"}
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        for owner, reference in (("builder-a", "visible-policy"), ("other", "private-policy")):
            response = await client.post("/v1/studio/knowledge/bases",
                headers={**headers, "X-User-ID": owner},
                json={"reference": reference, "displayName": reference, "sourceReferences": []})
            assert response.status_code == 201, response.text
        draft = (await client.post("/v1/studio/drafts", headers=headers,
                                  json=draft_request())).json()
        path = f"/v1/studio/drafts/{draft['draftId']}"
        tools = list(dict.fromkeys([*draft["spec"]["builtinTools"], "WebSearch", "WebFetch"]))
        model.complete_text.return_value = json.dumps({"reply": "联网和知识库绑定待确认",
            "changes": {"builtinTools": tools, "knowledgeReferences": ["visible-policy"]}})
        proposal = await client.post(path + "/builder-conversation", headers=headers,
            json={"expectedRevision": 1, "messages": [
                {"role": "user", "content": "启用联网，绑定知识库"}]})
        assert proposal.status_code == 200, proposal.text
        context = json.loads(model.complete_text.call_args.kwargs["user_prompt"])
        assert [item["reference"] for item in context["assemblyCatalog"]["knowledgeBases"]] == [
            "visible-policy"]
        changes = proposal.json()["changes"]
        assert changes["capabilityCatalogRevision"] >= 1
        assert (await client.get(path, headers=headers)).json()["revision"] == 1
        denied = await client.post(path + "/builder-apply", headers=headers,
            json={"expectedRevision": 1, "changes": {**changes,
                                                    "knowledgeReferences": ["private-policy"]}})
        assert denied.status_code == 409, denied.text
        saved = await client.post(path + "/builder-apply", headers=headers,
            json={"expectedRevision": 1, "changes": changes})
        assert saved.status_code == 200, saved.text
        assert saved.json()["spec"]["knowledgeReferences"] == ["visible-policy"]
        assert {"WebSearch", "WebFetch"} <= set(saved.json()["spec"]["builtinTools"])
        # Visibility is checked again at apply time, not only when proposing.
        deleted = await client.delete("/v1/studio/knowledge/bases/visible-policy", headers=headers)
        assert deleted.status_code in {200, 204}, deleted.text
        fresh = (await client.post("/v1/studio/drafts", headers=headers,
                                  json=draft_request("fresh-researcher"))).json()
        denied = await client.post(f"/v1/studio/drafts/{fresh['draftId']}/builder-apply",
            headers=headers, json={"expectedRevision": 1, "changes": changes})
        assert denied.status_code == 409, denied.text


@pytest.mark.asyncio
async def test_builder_authors_operator_model_and_limits_without_running_code() -> None:
    application = app()
    model = AsyncMock()
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}",
               "X-Tenant-ID": "tenant-a", "X-User-ID": "builder-a"}
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (await client.post("/v1/studio/drafts", headers=headers,
                                  json=draft_request())).json()
        path = f"/v1/studio/drafts/{draft['draftId']}"
        changes = {"model": draft["spec"]["model"],
            "limits": {**draft["spec"]["limits"], "timeoutSeconds": 120},
            "workspace": {**draft["spec"]["workspace"], "restoreSession": False},
            "pythonTools": [{"name": "double", "description": "输入翻倍",
                "inputSchema": {"type": "object", "properties": {"value": {"type": "number"}}},
                "code": "def run(arguments):\n    return arguments['value'] * 2\n"}]}
        model.complete_text.return_value = json.dumps({"reply": "配置待确认", "changes": changes})
        response = await client.post(path + "/builder-conversation", headers=headers,
            json={"expectedRevision": 1, "messages": [
                {"role": "user", "content": "添加翻倍算子，超时两分钟"}]})
        assert response.status_code == 200, response.text
        proposal = response.json()["changes"]
        assert proposal["capabilityCatalogRevision"] >= 1
        assert (await client.get(path, headers=headers)).json()["spec"]["pythonTools"] == []
        saved = await client.post(path + "/builder-apply", headers=headers,
            json={"expectedRevision": 1, "changes": proposal})
        assert saved.status_code == 200, saved.text
        assert saved.json()["spec"]["pythonTools"][0]["name"] == "double"
        assert saved.json()["spec"]["limits"]["timeoutSeconds"] == 120
        assert saved.json()["spec"]["workspace"]["restoreSession"] is False
        denied = await client.post(path + "/builder-apply", headers=headers,
            json={"expectedRevision": 2, "changes": {"capabilityCatalogRevision":
                proposal["capabilityCatalogRevision"],
                "model": {"routeId": "not-visible", "model": "secret"}}})
        assert denied.status_code == 409, denied.text
        denied = await client.post(path + "/builder-apply", headers=headers,
            json={"expectedRevision": 2, "changes": {"capabilityCatalogRevision":
                proposal["capabilityCatalogRevision"], "subagents": [{"alias": "private", "ref":
                "private-agent@0.1.0", "responsibility": "review"}]}})
        assert denied.status_code == 409, denied.text


def test_partial_builder_limits_preserve_unmentioned_values() -> None:
    from harness.studio.builder_conversation import BuilderChanges, apply_builder_changes
    from harness.studio.factory import create_draft_spec
    from harness.studio.models import CreateAgentDraftRequest

    request = CreateAgentDraftRequest.model_validate(draft_request())
    spec = create_draft_spec(name=request.name, domain=request.domain,
                             display_name=request.display_name, description=request.description,
                             template=request.template)
    spec = spec.model_copy(update={"limits": spec.limits.model_copy(update={"max_turns": 35})})
    candidate = apply_builder_changes(spec, BuilderChanges.model_validate(
        {"limits": {"timeoutSeconds": 120}, "workspace": {"restoreSession": False}}))
    assert candidate.limits.max_turns == 35
    assert candidate.limits.timeout_seconds == 120
    assert candidate.workspace.archive_on_complete is True
    assert candidate.workspace.restore_session is False
