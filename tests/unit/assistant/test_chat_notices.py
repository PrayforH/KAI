from urllib.parse import parse_qs, urlsplit

from harness.assistant.chat_notices import notice_text
from tests.integration.api.test_agui_chat_notices import notice


def test_failed_result_links_to_real_source_with_failure_reason():
    text = notice_text(notice())
    assert "未完成" in text and "下载超时" in text
    href = text.split("](")[-1].removesuffix(")")
    assert urlsplit(href).path == "/assistant"
    assert parse_qs(urlsplit(href).query) == {
        "bot": ["specialist_report"],
        "run": ["source-run"],
        "trace": ["1"],
        "checkpoint": ["checkpoint_execution-a"],
    }


def test_legacy_and_success_receipts_do_not_invent_failure_or_run_coordinates():
    text = notice_text(
        notice(kind="succeeded", runId=None, executionId=None, threadId="ordinary task")
    )
    assert "已完成" in text and "trace=" not in text and "run=" not in text
    assert "thread=ordinary+task" in text
    assert "](" not in notice_text(notice(threadId=None))


def test_title_cannot_inject_a_link_or_html():
    text = notice_text(notice(title="[click](https://example.com) <script>"))
    assert text.startswith(r"\[click\](https://example.com) \<script\>")
