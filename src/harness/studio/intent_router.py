"""Optional, bounded intent classification for automatic Builder conversations."""

from __future__ import annotations

import asyncio
import json
import logging
from time import monotonic
from typing import Literal, cast

import httpx
from pydantic import SecretStr

from harness.config import Settings
from harness.studio.builder_conversation import (
    BUILDER_AUTO_SYSTEM_PROMPT,
    BuilderConversationRequest,
    BuilderModelReply,
)
from harness.studio.models import AgentDraftSpec, StudioModel

_LOGGER = logging.getLogger(__name__)

INTENT_SYSTEM_PROMPT = """分类任务。只返回 JSON {"action":"类别"}，不得回答用户或执行任务。
类别：
edit = 修改智能体本身的长期配置/提示词/技能/工具/知识库；
run = 让智能体处理本次业务问题或本次产物；
rerun = 按已保存配置重跑上次任务；
ask = 无法分清是改智能体还是改本次答案；
reply = 询问智能体配置或构建方法。
判断的是最新用户消息。历史消息仅用于理解指代。
没有历史任务或明确修改对象时，“再短一点”“不太对”“换一种方式”必须 ask。
智能体名称和简介不能替用户指定本轮任务或修改对象。
例：以后都用表格回答 -> edit
例：添加知识库检索能力 -> edit
例：帮我查公司资料 -> run
例：把这次报告缩短 -> run
例：用保存后的配置再测一次 -> rerun
例：把智能体输出格式改成表格，然后再跑一次 -> edit
例：再短一点（无上下文） -> ask
例：这个智能体能做什么 -> reply
例：上文询问是否改默认行为，用户说“只改这次” -> run
例：上文询问是否改默认行为，用户说“以后都这样” -> edit
要求：配置修改后再跑，先 edit。只输出 action，不输出 reply/task/changes。
"""


class BuilderIntentDecision(StudioModel):
    action: Literal["edit", "run", "rerun", "ask", "reply"]


def direct_builder_reply(
    decision: BuilderIntentDecision, run_context: str
) -> BuilderModelReply | None:
    """Only clarification and an existing-task rerun need no authoring completion."""
    if decision.action == "ask":
        return BuilderModelReply(
            action="ask", reply="你希望修改智能体以后的行为，还是只调整这次回答？"
        )
    if decision.action not in {"run", "rerun"}:
        return None
    try:
        raw: object = json.loads(run_context or "{}")
    except ValueError:
        raw = {}
    context = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
    if context.get("pendingProposal"):
        return BuilderModelReply(action="ask", reply="请先应用或放弃当前修改建议，再开始试跑。")
    if decision.action == "run":
        return None  # The existing model fills task/history and can still clarify ambiguity.
    previous_task = context.get("task")
    if not isinstance(previous_task, str) or not previous_task.strip():
        return BuilderModelReply(action="ask", reply="还没有上次任务，请提供测试内容。")
    return BuilderModelReply(action="rerun", reply="将使用已保存配置重新执行上次任务。")


def _allowed_actions(decision: BuilderIntentDecision) -> set[str]:
    return {
        "edit": {"edit", "ask", "reply"},
        "run": {"run", "rerun", "ask", "reply"},
        "reply": {"ask", "reply"},
        "ask": {"ask"},
        "rerun": {"rerun", "ask"},
    }[decision.action]


def routed_builder_system_prompt(decision: BuilderIntentDecision) -> str:
    allowed = "、".join(sorted(_allowed_actions(decision)))
    return (
        BUILDER_AUTO_SYSTEM_PROMPT
        + f"\n本轮入口已判定用户意图为 {decision.action}。只可返回 {allowed}。"
        "如果最新消息存在歧义，无法确定改默认行为还是本次输出，必须 ask。"
        "任务不完整或原始材料缺失时也可 ask。结合历史补齐业务任务，不编造材料。"
        "不要声称已应用或已执行。"
    )


def validate_routed_action(decision: BuilderIntentDecision, action: str) -> bool:
    return action in _allowed_actions(decision)


class BuilderIntentRouter:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        model: str,
        timeout_seconds: float = 12,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    @classmethod
    def from_settings(cls, settings: Settings) -> BuilderIntentRouter | None:
        if not settings.studio_builder_intent_model:
            return None
        return cls(
            base_url=settings.studio_builder_intent_base_url,
            api_key=settings.studio_builder_intent_api_key,
            model=settings.studio_builder_intent_model,
            timeout_seconds=settings.studio_builder_intent_timeout_seconds,
        )

    async def classify(
        self, spec: AgentDraftSpec, request: BuilderConversationRequest
    ) -> BuilderIntentDecision | None:
        """On upstream/protocol failure, leave the existing auto route in control."""
        started = monotonic()
        context = {
            "currentAgent": {
                "displayName": spec.display_name,
                "description": spec.description,
                "taskContract": spec.task_contract.model_dump(by_alias=True)
                if spec.task_contract else None,
            },
            "conversation": [
                {"role": item.role, "content": item.content[:2_000]}
                for item in request.messages[-7:-1]
            ] + [request.messages[-1].model_dump()],
            "runContext": request.run_context,
        }
        headers = {"content-type": "application/json"}
        if self._api_key.get_secret_value():
            headers["authorization"] = f"Bearer {self._api_key.get_secret_value()}"
        client = self._http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_seconds, connect=min(3, self._timeout_seconds)),
            follow_redirects=False,
        )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                        ],
                        "temperature": 0,
                        "max_tokens": 64,
                        "response_format": {"type": "json_object"},
                        "stream": False,
                    },
                )
                response.raise_for_status()
                decision = self._parse_response(response.json())
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError, IndexError):
            # Never log upstream text, credentials, messages or run output.
            _LOGGER.info("builder_intent fallback model=%s elapsed_ms=%d",
                         self._model, (monotonic() - started) * 1000)
            return None
        finally:
            if self._http_client is None:
                await client.aclose()
        _LOGGER.info("builder_intent action=%s model=%s elapsed_ms=%d",
                     decision.action, self._model, (monotonic() - started) * 1000)
        return decision

    @staticmethod
    def _parse_response(payload: object) -> BuilderIntentDecision:
        if not isinstance(payload, dict):
            raise ValueError("invalid completion")
        values = cast(dict[str, object], payload)
        choices = values.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("invalid choices")
        choice = cast(dict[str, object], choices[0])
        if choice.get("finish_reason") != "stop":
            raise ValueError("incomplete classification")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("invalid message")
        text = cast(dict[str, object], message).get("content")
        if not isinstance(text, str):
            raise ValueError("invalid content")
        text = text.strip()
        if text.startswith("```") and text.endswith("```"):
            text = "\n".join(text.splitlines()[1:-1])
        return BuilderIntentDecision.model_validate_json(text)
