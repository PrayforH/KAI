from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.feishu.service import FeishuService


@pytest.mark.asyncio
@pytest.mark.parametrize("files,replies", [(["run"], []), ([], ["run"]), (["run"], ["run"])])
async def test_confirmed_owned_file_or_text_receipt_resolves_original_run(files, replies):
    session = AsyncMock()
    session.scalars.side_effect = [files, replies]
    conversation = AsyncMock()
    conversation.result_reference.return_value = {"threadId": "original", "messageId": "answer"}
    service = SimpleNamespace(settings=SimpleNamespace(app_id="app"), conversation=conversation)
    binding = SimpleNamespace(tenant_id="t", user_id="u", binding_id="binding")
    result = await FeishuService.result_reference(service, session, binding, "om_result")
    assert result["threadId"] == "original"
    conversation.result_reference.assert_awaited_once_with("t", "u", run_id="run")
    file_query, reply_query = [
        call.args[0].compile().params for call in session.scalars.call_args_list
    ]
    assert set(["feishu", "t", "u", "app:binding", "om_result", "sent"]).issubset(
        file_query.values()
    )
    assert set(["app", "binding", "om_result", "sent"]).issubset(reply_query.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("files,replies", [([], []), (["a"], ["b"])])
async def test_missing_or_ambiguous_receipt_never_guesses_run(files, replies):
    session = AsyncMock()
    session.scalars.side_effect = [files, replies]
    conversation = AsyncMock()
    service = SimpleNamespace(settings=SimpleNamespace(app_id="app"), conversation=conversation)
    binding = SimpleNamespace(tenant_id="t", user_id="u", binding_id="binding")
    assert await FeishuService.result_reference(service, session, binding, "om_result") is None
    conversation.result_reference.assert_not_awaited()
