import pytest

from harness.runtime.web_evidence import WebEvidence
from harness.runtime.web_tools import WEB_TOOL_NAMES


def evidence(**overrides):
    return WebEvidence.for_request(**{
        "assistant_scope": True, "prompt": "今天上海天气",
        "tools": set(WEB_TOOL_NAMES.values()), **overrides,
    })


def test_old_forecast_failure_needs_one_current_attempt():
    state = evidence()
    assert state.needs_verification("【上海 · 2026-11-03】今日预报未获取到。")
    assert not state.needs_verification("今日预报未获取到")


@pytest.mark.parametrize("tool", ["WebSearch", "mcp__harness-web__fetch"])
def test_an_actual_attempt_allows_honest_failure(tool):
    state = evidence()
    state.note_request(tool)
    assert not state.needs_verification("今日预报未获取到，来源连接失败")


@pytest.mark.parametrize("overrides", [
    {"assistant_scope": False}, {"tools": set()},
    {"prompt": "把‘今日预报未获取到’翻译成英文"},
    {"prompt": "解释‘天气预报’这个词"}, {"prompt": "读取这个文件"},
    {"prompt": "今天上海天气，不要联网查询"},
    {"prompt": "Shanghai weather, without web access"},
    {"prompt": "暂停旧天气职责，不需要再次查天气"},
])
def test_non_weather_work_and_disabled_web_are_unaffected(overrides):
    assert not evidence(**overrides).needs_verification("今日预报未获取到")


def test_a_normal_answer_and_active_stop_hook_are_not_retried():
    state = evidence()
    assert not state.needs_verification("我来查询一下")
    assert not state.needs_verification("今日预报未获取到", stop_hook_active=True)


def test_a_duty_management_error_is_not_a_failed_forecast():
    state = evidence(prompt="查询天气提醒职责的状态")
    assert not state.needs_verification("无法查询到对应职责，请检查名称")
