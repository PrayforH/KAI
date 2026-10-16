# pyright: reportPrivateUsage=false
import asyncio
import json
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from harness.agui import routes
from harness.agui.conversation_input import has_conversation_input
from harness.api.dependencies import ApiContainer, Identity
from harness.core.events import RunEvent
from harness.core.models import Run, RunStatus

FORM: dict[str, Any] = {
    "version": 1,
    "title": "请补充",
    "questions": [{"id": "password", "label": "密码", "type": "password", "options": []}],
}
TEXT = "```harness-input\n" + json.dumps(FORM, ensure_ascii=False) + "\n```"
NOW = datetime.now(UTC)


def run(identifier: str = "r1", status: str = "succeeded", tenant: str = "tenant") -> Run:
    return Run(
        run_id=identifier,
        session_id="session",
        tenant_id=tenant,
        status=RunStatus(status),
        idempotency_key=identifier,
        created_at=NOW,
        updated_at=NOW,
        input={"prompt": "查一下"},
    )


class ParserTests(unittest.TestCase):
    def test_accepts_all_client_fences_and_bare_form(self):
        for fence in ["harness-input", "harness", "json", ""]:
            self.assertTrue(has_conversation_input(TEXT.replace("harness-input", fence)))
        self.assertTrue(has_conversation_input("请回答：\n" + json.dumps(FORM)))

    def test_rejects_incomplete_example_or_ambiguous_forms(self):
        for text in [
            TEXT[:-5],
            "说明需要密码",
            TEXT + "\n" + TEXT,
            TEXT.replace("harness-input", "python"),
        ]:
            self.assertFalse(has_conversation_input(text))

    def test_rejects_invalid_protocol(self):
        for questions in [
            [],
            [{**FORM["questions"][0], "options": ["yes"]}],
            [{**FORM["questions"][0], "type": "single"}],
            [{**FORM["questions"][0], "sensitive": "true"}],
        ]:
            self.assertFalse(has_conversation_input(json.dumps({**FORM, "questions": questions})))
        self.assertFalse(has_conversation_input(json.dumps({**FORM, "extra": True})))

    def test_accepts_single_and_multi_choice(self):
        for kind in ["single", "multi", "text"]:
            options = [] if kind == "text" else ["继续", "停止"]
            self.assertTrue(
                has_conversation_input(
                    json.dumps(
                        {
                            **FORM,
                            "questions": [
                                {"id": "q", "label": "选择", "type": kind, "options": options}
                            ],
                        }
                    )
                )
            )

    def test_ignores_json_inside_an_unrelated_code_fence(self):
        self.assertFalse(
            has_conversation_input("```python\nexample = 1\n" + json.dumps(FORM) + "\n```")
        )


class StatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes._PENDING_INPUT_CACHE.clear()
        self.gate = asyncio.Semaphore(2)

    async def test_reads_real_snapshot_without_loading_activity_or_presentations(self):
        event = RunEvent(
            event_id="e",
            run_id="r1",
            tenant_id="tenant",
            session_id="session",
            sequence=1,
            type="history.snapshot",
            timestamp=NOW,
            payload={"version": routes._HISTORY_SNAPSHOT_VERSION, "response_text": TEXT},
        )
        events = AsyncMock(return_value=[event])
        container = SimpleNamespace(observed_events=SimpleNamespace(list_after=events))
        self.assertTrue(
            await routes._thread_pending_input(
                cast(ApiContainer, container), "tenant", run(), self.gate
            )
        )
        self.assertEqual(events.await_count, 1)
        assert events.await_args is not None
        self.assertEqual(events.await_args.kwargs["types"], ("history.snapshot",))

    async def test_reconstructs_legacy_text_without_writing_on_a_list_read(self):
        event = RunEvent(
            event_id="e",
            run_id="r1",
            tenant_id="tenant",
            session_id="session",
            sequence=1,
            type="message.delta",
            timestamp=NOW,
            payload={"text": TEXT, "message_id": "assistant-r1"},
        )
        events = AsyncMock(side_effect=[[], [event]])
        container = SimpleNamespace(observed_events=SimpleNamespace(list_after=events))
        self.assertTrue(
            await routes._thread_pending_input(
                cast(ApiContainer, container), "tenant", run(), self.gate
            )
        )
        self.assertEqual(events.await_count, 2)

    async def test_only_successfully_completed_form_needs_input(self):
        with patch.object(
            routes, "_pending_input_response", AsyncMock(return_value=TEXT)
        ) as project:
            self.assertTrue(
                await routes._thread_pending_input(
                    cast(ApiContainer, SimpleNamespace()), "tenant", run(), self.gate
                )
            )
            for state in ["running", "queued", "cancelled", "failed", "timed_out"]:
                self.assertFalse(
                    await routes._thread_pending_input(
                        cast(ApiContainer, SimpleNamespace()),
                        "tenant",
                        run(status=state),
                        self.gate,
                    )
                )
            self.assertEqual(project.await_count, 1)

    async def test_cache_stores_only_boolean_and_is_tenant_scoped(self):
        with patch.object(
            routes, "_pending_input_response", AsyncMock(return_value=TEXT)
        ) as project:
            for tenant in ["tenant", "tenant", "other"]:
                self.assertTrue(
                    await routes._thread_pending_input(
                        cast(ApiContainer, SimpleNamespace()), tenant, run(tenant=tenant), self.gate
                    )
                )
            self.assertEqual(project.await_count, 2)
            self.assertTrue(
                all(type(value) is bool for value in routes._PENDING_INPUT_CACHE.values())
            )

    async def test_retries_when_terminal_text_is_not_yet_visible(self):
        with patch.object(routes, "_pending_input_response", AsyncMock(side_effect=["", TEXT])):
            self.assertFalse(
                await routes._thread_pending_input(
                    cast(ApiContainer, SimpleNamespace()), "tenant", run(), self.gate
                )
            )
            self.assertTrue(
                await routes._thread_pending_input(
                    cast(ApiContainer, SimpleNamespace()), "tenant", run(), self.gate
                )
            )

    async def test_projection_failure_does_not_break_list_or_cache_failure(self):
        with patch.object(
            routes, "_pending_input_response", AsyncMock(side_effect=RuntimeError("temporary"))
        ):
            self.assertFalse(
                await routes._thread_pending_input(
                    cast(ApiContainer, SimpleNamespace()), "tenant", run(), self.gate
                )
            )
            self.assertEqual(len(routes._PENDING_INPUT_CACHE), 0)

    async def test_list_survives_reload_and_new_reply_clears_indicator(self):
        first = run()
        binding = SimpleNamespace(
            thread_id="thread",
            session_id="session",
            session_ids=["session"],
            created_at=NOW,
            updated_at=NOW,
            archived_at=None,
            pinned_at=None,
            last_read_at=NOW,
            project_id=None,
        )
        session = SimpleNamespace(
            session_id="session",
            agent_name="agent",
            agent_version="1",
            resolved_agent_owner_user_id="user",
            team_ids=[],
        )
        container = SimpleNamespace(
            agui=SimpleNamespace(
                list_bindings=AsyncMock(return_value=[binding]),
                resolve_title=AsyncMock(return_value="任务"),
            ),
            sessions=SimpleNamespace(list_for_ids=AsyncMock(return_value=[session])),
            runs=SimpleNamespace(list_for_sessions=AsyncMock(return_value=[first])),
            approvals=SimpleNamespace(list_for_runs=AsyncMock(return_value=[])),
        )
        identity = Identity(tenant_id="tenant", user_id="user")
        with patch.object(routes, "_pending_input_response", AsyncMock(return_value=TEXT)):
            for _ in range(2):
                tasks = await routes.list_agui_threads(
                    identity, cast(ApiContainer, container), 50, False
                )
                self.assertTrue(tasks[0].pending_input)
                self.assertEqual(tasks[0].status, "succeeded")
                self.assertNotIn("questions", tasks[0].model_dump_json())
            reply = run("reply", "running").model_copy(
                update={
                    "created_at": NOW + timedelta(seconds=1),
                    "updated_at": NOW + timedelta(seconds=1),
                }
            )
            container.runs.list_for_sessions.return_value = [first, reply]
            tasks = await routes.list_agui_threads(
                identity, cast(ApiContainer, container), 50, False
            )
            self.assertFalse(tasks[0].pending_input)


if __name__ == "__main__":
    unittest.main()


class PaginatedInputTests(unittest.TestCase):
    def test_large_option_lists_still_request_input(self):
        for count in (9, 32, 128):
            form = {
                "version": 1,
                "title": "候选确认",
                "questions": [
                    {
                        "id": "scope",
                        "label": "选择范围",
                        "type": "multi",
                        "options": [f"候选 {i}" for i in range(count)],
                    }
                ],
            }
            self.assertTrue(
                has_conversation_input("```harness-input\n" + json.dumps(form) + "\n```")
            )
        form["questions"][0]["options"].append("超出传输边界")
        self.assertFalse(has_conversation_input(json.dumps(form)))

    def test_question_count_is_independent_of_a_screen(self):
        form = {
            "version": 1,
            "title": "补充信息",
            "questions": [{"id": f"q{i}", "label": f"问题 {i}", "type": "text"} for i in range(5)],
        }
        self.assertTrue(has_conversation_input(json.dumps(form)))
