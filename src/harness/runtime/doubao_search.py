"""Doubao Custom/Global search, using the Sapling 2.0 API-key wire contract."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from itertools import zip_longest
from typing import Any, cast

import httpx

from harness.runtime.web_tools import PublicWebClient, WebAccessError, check_public_query

_BASE = "https://open.feedcoopapi.com/search_api/"


def _object(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _items(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise WebAccessError("豆包搜索返回无效结果列表。")
    return [
        cast(dict[str, Any], item) for item in cast(list[Any], value)[:40] if isinstance(item, dict)
    ]


async def _request(
    client: httpx.AsyncClient, endpoint: str, body: dict[str, Any], key: str
) -> dict[str, Any]:
    try:
        async with client.stream(
            "POST", _BASE + endpoint, json=body, headers={"Authorization": f"Bearer {key}"}
        ) as response:
            if response.status_code != 200:
                raise WebAccessError(f"豆包搜索返回 HTTP {response.status_code}，请稍后重试。")
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 2_000_000:
                    raise WebAccessError("豆包搜索响应超过读取上限。")
        payload = _object(json.loads(raw))
        metadata = _object(payload.get("ResponseMetadata"))
        result = payload.get("Result")
        if metadata.get("Error") or (
            isinstance(result, dict) and _object(result).get("ErrorCode") not in (None, 0, "0")
        ):
            # Upstream error text may echo credentials or request values.
            raise WebAccessError("豆包搜索拒绝请求，请检查密钥、额度及服务权限。")
        if not isinstance(result, dict):
            raise WebAccessError("豆包搜索返回无效数据。")
        return cast(dict[str, Any], result)
    except (httpx.HTTPError, ValueError) as error:
        if isinstance(error, WebAccessError):
            raise
        raise WebAccessError("豆包搜索暂时不可用，请稍后重试。") from error


def _image(value: Any, source: str, description: Any = "") -> dict[str, str] | None:
    url = PublicWebClient.result_url(value)
    if not url or not url.startswith("https://"):
        return None
    return {"url": url, "source_url": source, "description": str(description or "")[:500]}


def _parse(
    result: dict[str, Any], *, global_index: bool, image_search: bool, include_images: bool
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    sources: list[dict[str, Any]] = []
    images: list[dict[str, str]] = []
    field = "Documents" if global_index else "ImageResults" if image_search else "WebResults"
    for item in _items(result.get(field)):
        source = PublicWebClient.result_url(item.get("Url"))
        if not source:
            continue
        title = str(item.get("Title") or "")[:300]
        inline: list[dict[str, str]] = []
        if global_index:
            parts = _items(item.get("Snippet") or item.get("Parts"))
            content = "\n".join(str(part.get("Text") or "")[:4000] for part in parts)
            published = _object(item.get("DocumentInfo")).get("PublishTime")
            if include_images:
                for part in parts:
                    info = _object(part.get("Image"))
                    image = _image(info.get("ImageUrl"), source, info.get("Alt") or title)
                    if image:
                        inline.append(image)
        else:
            content = str(item.get("Content") or item.get("Summary") or item.get("Snippet") or "")
            published = item.get("PublishTime")
            if include_images:
                if image_search:
                    info = _object(item.get("Image"))
                    description = _object(info.get("Features")).get("Description") or title
                    image = _image(info.get("Url"), source, description)
                    if image:
                        inline.append(image)
                else:
                    for info in _items(item.get("InlineImages")):
                        image = _image(info.get("ImageUrl"), source, info.get("Alt") or title)
                        if image:
                            inline.append(image)
        images.extend(inline[:3] if not image_search else inline[:8])
        if not image_search:
            sources.append(
                {
                    "url": source,
                    "title": title,
                    "content": content[:4000],
                    "truncated": len(content) > 4000,
                    "images": inline[:3],
                    "published_date": str(published or "")[:100],
                    "index": "global" if global_index else "cn",
                }
            )
    return sources, images


async def search(
    query: str,
    *,
    key: str,
    proxy: str | None = None,
    include_images: bool = True,
    max_results: int = 5,
    topic: str = "general",
    time_range: str = "",
    search_depth: str = "basic",
) -> dict[str, Any]:
    query = check_public_query(query, limit=100)
    if not key:
        raise WebAccessError("尚未配置豆包搜索密钥，请在联网设置中配置或联系管理员。")
    if (
        topic not in {"general", "news", "finance"}
        or time_range not in {"", "day", "week", "month", "year"}
        or search_depth not in {"basic", "advanced"}
    ):
        raise WebAccessError("搜索参数无效。")
    if type(include_images) is not bool or type(max_results) is not int:
        raise WebAccessError("搜索参数格式无效。")
    count = max(1, min(max_results, 10))
    requests = [
        (
            "cn_web",
            "web_search",
            {
                "Query": query,
                "SearchType": "web",
                "Count": count,
                "NeedSummary": True,
                "ContentFormats": "markdown",
            },
        ),
        (
            "global_web",
            "global_search",
            {
                "Query": query,
                "SearchType": "web",
                "DocCount": count,
                "MaxSnippetLength": 2000,
                "MaxImageCountPerDoc": 3 if include_images else 0,
            },
        ),
    ]
    if include_images:
        requests.extend(
            [
                ("cn_image", "web_search", {"Query": query, "SearchType": "image", "Count": 5}),
                (
                    "global_image",
                    "global_search",
                    {"Query": query, "SearchType": "image", "DocCount": 5},
                ),
            ]
        )
    try:
        async with (
            asyncio.timeout(40),
            httpx.AsyncClient(
                timeout=httpx.Timeout(30, connect=8),
                trust_env=False,
                follow_redirects=False,
                proxy=proxy,
            ) as client,
        ):
            responses = await asyncio.gather(
                *(_request(client, endpoint, body, key) for _, endpoint, body in requests),
                return_exceptions=True,
            )
    except TimeoutError as error:
        raise WebAccessError("豆包搜索超时，请稍后重试。") from error
    sources: list[dict[str, Any]] = []
    images: list[dict[str, str]] = []
    statuses: dict[str, str] = {}
    for (name, _, _), response in zip(requests, responses, strict=True):
        if isinstance(response, BaseException):
            statuses[name] = "failed"
            continue
        try:
            found, pictures = _parse(
                response,
                global_index=name.startswith("global"),
                image_search=name.endswith("image"),
                include_images=include_images,
            )
        except WebAccessError:
            statuses[name] = "failed"
            continue
        statuses[name] = "ok"
        sources.extend(found)
        if name.endswith("image"):
            images[0:0] = pictures
        else:
            images.extend(pictures)
    if all(value == "failed" for value in statuses.values()):
        raise WebAccessError("豆包搜索不可用，请检查密钥、额度或稍后重试。")
    # Interleave domestic/global results so a full domestic page does not hide global hits.
    cn = [item for item in sources if item["index"] == "cn"]
    global_ = [item for item in sources if item["index"] == "global"]
    ordered = [item for pair in zip_longest(cn, global_) for item in pair if item is not None]
    unique_sources = list({item["url"]: item for item in reversed(ordered)}.values())[::-1][:count]
    unique_images = list({item["url"]: item for item in reversed(images)}.values())[::-1][:8]
    notices = ["图片地址由搜索服务提供，部分为限时签名地址；引用请使用 source_url。"]
    if "failed" in statuses.values():
        notices.append("部分检索通道失败，当前为部分结果；详见 channels，不能据此断言未找到。")
    if topic != "general" or time_range or search_depth != "basic":
        notices.append(
            "豆包当前使用通用检索，未应用主题、时间范围或深度过滤；严格过滤请使用 Tavily。"
        )
    return {
        "query": query,
        "provider": "doubao",
        "trust": "untrusted",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "results": unique_sources,
        "images": unique_images,
        "channels": statuses,
        "partial": "failed" in statuses.values(),
        "images_status": "not_requested"
        if not include_images
        else "available"
        if unique_images
        else "unavailable"
        if "failed" in statuses.values()
        else "empty",
        "notices": notices,
    }
