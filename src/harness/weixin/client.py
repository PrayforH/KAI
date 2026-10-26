"""Small iLink adapter based on Tencent/openclaw-weixin's published protocol.

Never log request URLs, QR identifiers, payloads or credentials. Redirects are
accepted only for HTTPS hosts in the Weixin domain, without ports or userinfo.
"""

import base64
import secrets
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

BASE_URL = "https://ilinkai.weixin.qq.com"
VERSION = "2.4.8"


class WeixinError(Exception):
    def __init__(self, code: str = "NetworkUnavailable") -> None:
        self.code = code
        super().__init__(code)


def trusted_base(value: str) -> str:
    try:
        url = urlsplit(value if "://" in value else "https://" + value)
        if (
            url.scheme != "https"
            or not url.hostname
            or not url.hostname.endswith(".weixin.qq.com")
            or url.username
            or url.password
            or url.port not in (None, 443)
            or url.path not in ("", "/")
            or url.query
            or url.fragment
        ):
            raise ValueError
        return "https://" + url.hostname
    except ValueError:
        raise WeixinError("InvalidServerAddress") from None


class WeixinClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def request(
        self,
        endpoint: str,
        *,
        base: str = BASE_URL,
        token: str = "",
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"iLink-App-Id": "bot", "iLink-App-ClientVersion": str(2 << 16 | 4 << 8 | 8)}
        if params is None:
            headers.update(
                {
                    "AuthorizationType": "ilink_bot_token",
                    "X-WECHAT-UIN": base64.b64encode(str(secrets.randbits(32)).encode()).decode(),
                }
            )
            if token:
                headers["Authorization"] = "Bearer " + token
            if body is not None and endpoint != "get_bot_qrcode?bot_type=3":
                body = dict(body, base_info={"channel_version": VERSION, "bot_agent": "KAI/1.0"})
        try:
            async with httpx.AsyncClient(
                transport=self.transport, trust_env=False, timeout=40, follow_redirects=False
            ) as client:
                url = trusted_base(base) + "/ilink/bot/" + endpoint
                response = (
                    await client.get(url, params=params, headers=headers)
                    if params is not None
                    else await client.post(url, json=body or {}, headers=headers)
                )
                response.raise_for_status()
                value = response.json()
                if not isinstance(value, dict):
                    raise ValueError
                value = cast(dict[str, Any], value)
                if value.get("ret", 0) or value.get("errcode", 0):
                    code = (
                        "AuthorizationExpired"
                        if -14 in (value.get("ret"), value.get("errcode"))
                        else "PlatformRejected"
                    )
                    raise WeixinError(code)
                return value
        except (httpx.HTTPError, ValueError):
            raise WeixinError() from None

    async def qr(self) -> dict[str, Any]:
        return await self.request("get_bot_qrcode?bot_type=3", body={"local_token_list": []})

    async def qr_status(self, qrcode: str, base: str, verify_code: str = "") -> dict[str, Any]:
        params = {"qrcode": qrcode}
        if verify_code:
            params["verify_code"] = verify_code
        return await self.request("get_qrcode_status", base=base, params=params)

    async def updates(self, credentials: dict[str, str], cursor: str) -> dict[str, Any]:
        return await self.request(
            "getupdates",
            base=credentials["base"],
            token=credentials["token"],
            body={"get_updates_buf": cursor},
        )

    async def send(
        self, credentials: dict[str, str], context: str, client_id: str, text: str
    ) -> None:
        await self.request(
            "sendmessage",
            base=credentials["base"],
            token=credentials["token"],
            body={
                "msg": {
                    "from_user_id": "",
                    "to_user_id": credentials["peer"],
                    "client_id": client_id,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": context,
                    "item_list": [{"type": 1, "text_item": {"text": text}}],
                }
            },
        )
