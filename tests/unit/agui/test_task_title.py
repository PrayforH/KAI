import json
from typing import cast

import httpx
import pytest
from pydantic import SecretStr

from harness.agui.task_title import (
    MAX_TASK_TITLE_LENGTH,
    AnthropicCompatibleTaskTitleGenerator,
    ControlPlaneTaskTitleGenerator,
    summarize_task_title,
    summarize_task_title_from_prompts,
)
from harness.studio.model_configuration import (
    ModelConfiguration,
    ModelConfigurationList,
    ModelConfigurationService,
)


def test_summarizes_polite_chinese_request_without_losing_the_intent() -> None:
    assert (
        summarize_task_title("请帮我查询下世界杯在抖音上的舆论走向，谢谢")
        == "查询世界杯在抖音上的舆论走向"
    )


def test_fallback_discards_rejected_process_and_keeps_final_output() -> None:
    assert summarize_task_title("不写入了，先单次生成可下载报告") == "生成可下载报告"


def test_summarizes_long_multistep_request_to_a_stable_short_label() -> None:
    title = summarize_task_title(
        "请分析 2026 年 7 月中国 AI 监管政策动态与舆论反应，"
        "并生成一份包含时间线、风险和建议的完整报告"
    )

    assert title == "分析2026年7月中国AI监管政策动态与舆论反应"
    assert len(title) <= MAX_TASK_TITLE_LENGTH


def test_normalizes_markdown_links_and_greetings() -> None:
    assert summarize_task_title("你好！") == "日常问候"
    assert summarize_task_title("- 请分析 https://example.com 的安全风险") == "分析链接的安全风险"


def test_preserves_short_meaningful_titles_and_handles_empty_input() -> None:
    assert summarize_task_title("first task") == "first task"
    assert summarize_task_title("") == "新任务"


def test_truncates_an_unbroken_long_subject_with_an_ellipsis() -> None:
    title = summarize_task_title("超长任务名称" * 10)

    assert len(title) == MAX_TASK_TITLE_LENGTH
    assert title.endswith("…")


def test_multiturn_title_replaces_a_greeting_with_the_real_request() -> None:
    assert summarize_task_title_from_prompts(
        ["你好", "帮我分析世界杯在抖音上的舆论走向"]
    ) == "分析世界杯在抖音上的舆论走向"


def test_multiturn_title_keeps_subject_for_a_followup_request() -> None:
    title = summarize_task_title_from_prompts(
        ["分析世界杯在抖音上的舆论走向", "另外补充关键传播节点和影响人物"]
    )

    assert "世界杯" in title
    assert "传播节点" in title
    assert len(title) <= MAX_TASK_TITLE_LENGTH


def test_multiturn_title_tracks_a_substantive_topic_change() -> None:
    assert summarize_task_title_from_prompts(
        ["分析世界杯舆情", "排查用户编辑消息后没有重新运行的问题"]
    ) == "排查用户编辑消息后没有重新运行的问题"


@pytest.mark.asyncio
async def test_model_generator_requests_a_semantic_title_and_cleans_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "标题：生成可下载报告\n"}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    generator = AnthropicCompatibleTaskTitleGenerator(
        base_url="https://gateway.example/anthropic",
        model="fast-model",
        credential=SecretStr("secret-token"),
        provider="new-api",
        http_client=client,
    )

    title = await generator.generate(
        "tenant-a",
        "user-a",
        ["不写入了", "先单次生成可下载报告"],
    )
    await client.aclose()

    assert title == "生成可下载报告"
    assert requests[0].url == "https://gateway.example/anthropic/v1/messages"
    assert requests[0].headers["authorization"] == "Bearer secret-token"
    assert "最终想完成的目标或产物".encode() in requests[0].content
    assert json.loads(requests[0].content)["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_control_plane_generator_prefers_a_configured_lightweight_route() -> None:
    calls: list[tuple[str, str]] = []

    class FakeModels:
        async def list(self, tenant_id: str) -> ModelConfigurationList:
            assert tenant_id == "tenant-a"
            return ModelConfigurationList(
                revision=1,
                models=(
                    ModelConfiguration(
                        routeId="large-model",
                        label="Large",
                        modelType="chat",
                        provider="example",
                        model="large-model",
                        baseUrl="https://models.example/v1",
                        apiFormat="openai_compatible",
                        authScheme="bearer",
                        capabilities=("streaming", "tool_use"),
                        enabled=True,
                        credentialConfigured=True,
                        deletable=True,
                        version=1,
                    ),
                    ModelConfiguration(
                        routeId="deepseek-flash",
                        label="Flash",
                        modelType="chat",
                        provider="example",
                        model="deepseek-v4-flash",
                        baseUrl="https://models.example/v1",
                        apiFormat="openai_compatible",
                        authScheme="bearer",
                        capabilities=("streaming", "tool_use"),
                        enabled=True,
                        credentialConfigured=True,
                        deletable=True,
                        version=1,
                    ),
                ),
                agentModelBindings={},
            )

        async def complete_text(
            self,
            tenant_id: str,
            route_id: str,
            **kwargs: object,
        ) -> str:
            calls.append((tenant_id, route_id))
            assert "最终想完成的目标或产物" in cast(str, kwargs["system_prompt"])
            return "标题：生成视频宣传片"

    generator = ControlPlaneTaskTitleGenerator(
        cast(ModelConfigurationService, FakeModels())
    )
    title = await generator.generate(
        "tenant-a",
        "user-a",
        ["先做图片", "改为生成视频宣传片"],
    )

    assert title == "生成视频宣传片"
    assert calls == [("tenant-a", "deepseek-flash")]
