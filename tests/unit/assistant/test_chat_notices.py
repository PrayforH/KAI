from urllib.parse import parse_qs, urlsplit

from harness.assistant.chat_notices import notice_href, notice_text
from tests.integration.api.test_agui_chat_notices import notice


def test_failed_result_links_to_real_source_with_failure_reason():
    text = notice_text(notice())
    assert "未完成" in text and "下载超时" in text
    href = notice_href(notice())
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
    assert "下载超时" in text and "回到原对话" not in text
    href = notice_href(
        notice(kind="succeeded", runId=None, executionId=None, threadId="ordinary task")
    )
    assert "trace=" not in href and "run=" not in href
    assert "thread=ordinary+task" in href
    assert "](" not in notice_text(notice(threadId=None))


def test_title_cannot_inject_a_link_or_html():
    text = notice_text(notice(title="[click](https://example.com) <script>"))
    assert "click" not in text and "<script>" not in text
    assert notice_text(notice(kind="succeeded", content="")) == "本次处理已结束。"


def test_main_bot_gets_a_brief_excerpt_while_late_blockers_are_preserved():
    report = ("侧栏功能已实现。\n\n" + "实现细节说明。" * 100
              + "\n真实页面尚未验证，需要你完成登录。")
    item = notice(kind="succeeded", content=report)
    text = notice_text(item)
    assert len(text) < 425
    assert "侧栏功能已实现。" in text and "尚未验证" in text and "需要你" in text
    assert item.content == report
    assert "已完成" not in text
