import pytest

from harness.studio.agent_builder import summarize_agent_display_name, summarize_agent_name


@pytest.mark.parametrize(
    ("task", "expected"),
    (
        (
            "作为育儿专家智能体，服务 0-12 岁孩子家长，覆盖喂养、睡眠和生长发育。",
            "育儿专家智能体",
        ),
        ("搜索最新互联网舆情，分析风险并生成可下载报告。", "互联网舆情助手"),
        ("办公文档助手，各种 Office 能力，可以做 PPT、Word 和 Excel。", "办公文档助手"),
        ("整理指定公司的公开信息，给出投资风险摘要。", "公司公开信息助手"),
        (
            "你能帮我做类案分析吗\n\n当前部署约束：只使用 Worker 环境。",
            "类案分析助手",
        ),
    ),
)
def test_summarize_agent_display_name(task: str, expected: str) -> None:
    assert summarize_agent_display_name(task) == expected


@pytest.mark.parametrize(
    ("task", "expected"),
    (
        ("作为育儿专家智能体，服务 0-12 岁孩子家长。", "parenting-expert"),
        ("搜索最新互联网舆情，分析风险并生成报告。", "sentiment-analyst"),
        ("办公文档助手，可以做 PPT、Word 和 Excel。", "office-assistant"),
        ("构建一个投资分析助手，跟踪财报和风险。", "investment-assistant"),
        ("你能帮我做类案分析吗", "case-analysis-assistant"),
    ),
)
def test_summarize_agent_name(task: str, expected: str) -> None:
    assert summarize_agent_name(task) == expected
