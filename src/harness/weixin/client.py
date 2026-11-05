"""Small iLink adapter based on Tencent/openclaw-weixin's published protocol.

Never log request URLs, QR identifiers, payloads or credentials. Redirects are
accepted only for HTTPS hosts in the Weixin domain, without ports or userinfo.
"""

import base64
import hashlib
import secrets
from typing import Any, cast
from urllib.parse import urlencode, urlsplit

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
        await self.send_item(
            credentials, context, client_id, {"type": 1, "text_item": {"text": text}}
        )

    async def send_item(
        self, credentials: dict[str, str], context: str, client_id: str, item: dict[str, Any]
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
                    "item_list": [item],
                }
            },
        )

    async def upload_file(
        self, credentials: dict[str, str], name: str, content: bytes
    ) -> dict[str, Any]:
        """Tencent's encrypted CDN upload, followed by a native FILE item."""
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        from harness.feishu.media import MediaRejectedError
        from harness.weixin.media import WeixinMediaSettings, cdn_url

        if not content or len(content) > WeixinMediaSettings().max_resource_bytes:
            raise MediaRejectedError("微信文件为空或超过发送上限，请在网页下载。")
        key, filekey = secrets.token_bytes(16), secrets.token_hex(16)
        padder = padding.PKCS7(128).padder()
        padded = padder.update(content) + padder.finalize()
        encoder = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        encrypted = encoder.update(padded) + encoder.finalize()
        result = await self.request(
            "getuploadurl",
            base=credentials["base"],
            token=credentials["token"],
            body={
                "filekey": filekey,
                "media_type": 3,
                "to_user_id": credentials["peer"],
                "rawsize": len(content),
                "rawfilemd5": hashlib.md5(content, usedforsecurity=False).hexdigest(),
                "filesize": len(encrypted),
                "no_need_thumb": True,
                "aeskey": key.hex(),
            },
        )
        url = result.get("upload_full_url")
        if not url:
            param = result.get("upload_param")
            if not isinstance(param, str) or not param:
                raise WeixinError("UploadNotConfirmed")
            url = "https://novac2c.cdn.weixin.qq.com/c2c/upload?" + urlencode(
                {"encrypted_query_param": param, "filekey": filekey}
            )
        url = cdn_url({"full_url": url})
        try:
            async with httpx.AsyncClient(
                transport=self.transport, trust_env=False, timeout=45, follow_redirects=False
            ) as client:
                response = await client.post(
                    url, content=encrypted, headers={"Content-Type": "application/octet-stream"}
                )
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    raise MediaRejectedError("微信拒绝了文件上传，请在网页下载。")
                response.raise_for_status()
                param = response.headers.get("x-encrypted-param")
                if response.status_code != 200 or not param:
                    raise WeixinError("UploadNotConfirmed")
        except httpx.HTTPError:
            raise WeixinError("UploadUnavailable") from None
        return {
            "type": 4,
            "file_item": {
                "file_name": name,
                "len": str(len(content)),
                "media": {
                    "encrypt_query_param": param,
                    "aes_key": base64.b64encode(key.hex().encode()).decode(),
                    "encrypt_type": 1,
                },
            },
        }
