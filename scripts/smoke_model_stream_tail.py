"""Verify a configured model's SSE text, including its final token chunk.

Run inside the API environment, e.g.:
  python scripts/smoke_model_stream_tail.py --route shdata-model
Optional --channel selects a New API channel using an existing admin-owned key.
Credentials remain in memory. The probe prints counts/status, never raw events.
"""

import argparse
import asyncio
import json
import re

import httpx
from pydantic import SecretStr

from harness.composition import build_production_container
from harness.config import Settings


async def probe(tenant: str, route_id: str, channel: int | None, repeats: int) -> bool:
    container = build_production_container(Settings())
    try:
        service = container.model_configurations
        assert service is not None
        route = await service.resolve_runtime(
            tenant, "stream-tail-probe", route_id, apply_agent_binding=False,
            required_api_format="anthropic_compatible",
        )
        if route is None:
            raise ValueError("an enabled Anthropic-compatible route with credentials is required")
        secret = route.credential
        if channel is not None:
            base_key = re.sub(r"-\d+$", "", secret.get_secret_value())
            secret = SecretStr(f"{base_key}-{channel}")
        base = route.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        expected = "\n".join(
            f"{index:02d}：春风吹过山谷，溪水流向远方。" for index in range(1, 21)
        ) + "\n验证结束标记：END-739182。"
        payload = {
            "model": route.model, "max_tokens": 2048, "temperature": 0,
            "stream": True, "thinking": {"type": "disabled"},
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": "请逐字输出，不解释：\n" + expected}],
        }
        headers = {"anthropic-version": "2023-06-01"}
        if route.resolved_auth_scheme == "x-api-key":
            headers["x-api-key"] = secret.get_secret_value()
        else:
            headers["authorization"] = f"Bearer {secret.get_secret_value()}"
        passed = True
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            for attempt in range(repeats):
                chunks: list[str] = []
                ended = False
                async with client.stream(
                    "POST", base + "/messages", headers=headers,
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                            continue
                        event = json.loads(line[5:].strip())
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            chunks.append(delta.get("text", ""))
                        ended = ended or event.get("type") == "message_stop"
                text = "".join(chunks)
                complete = text == expected and ended
                passed = passed and complete
                print(json.dumps({
                    "attempt": attempt + 1, "route": route_id, "channel": channel,
                    "complete": complete, "expected_chars": len(expected),
                    "actual_chars": len(text), "end_marker_present": text.endswith("END-739182。"),
                    "message_stop": ended,
                }), flush=True)
        return passed
    finally:
        if container.close:
            await container.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="local")
    parser.add_argument("--route", required=True)
    parser.add_argument("--channel", type=int)
    parser.add_argument("--repeats", type=int, default=2, choices=range(1, 11))
    args = parser.parse_args()
    passed = asyncio.run(probe(args.tenant, args.route, args.channel, args.repeats))
    raise SystemExit(0 if passed else 1)
