"""One bounded, non-streaming JSON classification call for UI decisions."""

import asyncio
import json
from typing import Any

import httpx
from pydantic import SecretStr


class UICompletionClient:
    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        model: str,
        *,
        timeout_seconds: float = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self._timeout = timeout_seconds
        self._transport = transport

    async def classify(
        self,
        prompt: str,
        data: dict[str, Any],
        *,
        max_tokens: int,
    ) -> str:
        async with (
            asyncio.timeout(self._timeout),
            httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=min(3, self._timeout)),
                transport=self._transport,
                trust_env=False,
                follow_redirects=False,
            ) as client,
        ):
            response = await client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
                json={
                    "model": self.model,
                    "temperature": 0,
                    "max_tokens": max_tokens,
                    "stream": False,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
                    ],
                },
            )
            response.raise_for_status()
            return completion_content(response.json())


def completion_content(payload: Any) -> str:
    try:
        choice = payload["choices"][0]
        content = choice["message"]["content"]
        if choice["finish_reason"] != "stop" or not isinstance(content, str):
            raise ValueError("incomplete or invalid UI classification")
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("invalid UI classification response") from error
    content = content.strip()
    if content.startswith("```") and content.endswith("```"):
        content = "\n".join(content.splitlines()[1:-1])
    return content
