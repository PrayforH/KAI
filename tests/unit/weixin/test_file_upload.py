import base64
import json

import httpx
import pytest

from harness.feishu.media import MediaRejectedError
from harness.weixin.client import WeixinClient, WeixinError
from harness.weixin.media import decrypt


@pytest.mark.asyncio
async def test_official_file_upload_encrypts_bytes_and_sends_native_file():
    original = b"report contents\n"
    uploaded = {}

    def handle(request):
        if request.url.path.endswith("getuploadurl"):
            body = json.loads(request.content)
            uploaded["key"] = body["aeskey"]
            assert body["rawsize"] == len(original) and body["media_type"] == 3
            assert body["filesize"] % 16 == 0
            return httpx.Response(200, json={"upload_param": "private-upload-param"})
        if request.url.path == "/c2c/upload":
            assert "Authorization" not in request.headers
            assert request.url.params["encrypted_query_param"] == "private-upload-param"
            media = {"aes_key": base64.b64encode(uploaded["key"].encode()).decode()}
            assert decrypt(request.content, {"media": media}, "file") == original
            return httpx.Response(200, headers={"x-encrypted-param": "private-download-param"})
        assert request.url.path.endswith("sendmessage")
        msg = json.loads(request.content)["msg"]
        assert msg["to_user_id"] == "peer" and msg["context_token"] == "context"
        file = msg["item_list"][0]
        assert file["type"] == 4 and file["file_item"]["file_name"] == "报告.txt"
        assert file["file_item"]["len"] == str(len(original))
        return httpx.Response(200, json={"ret": 0})

    client = WeixinClient(httpx.MockTransport(handle))
    credentials = {"base": "https://ilinkai.weixin.qq.com", "token": "private", "peer": "peer"}
    item = await client.upload_file(credentials, "报告.txt", original)
    await client.send_item(credentials, "context", "stable-id", item)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", ["https://127.0.0.1/private", "https://cdn.weixin.qq.com.evil.test/upload"]
)
async def test_upload_url_cannot_leak_content_or_token(value):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"upload_full_url": value})

    client = WeixinClient(httpx.MockTransport(handle))
    with pytest.raises(MediaRejectedError):
        await client.upload_file(
            {"base": "https://ilinkai.weixin.qq.com", "token": "private", "peer": "p"},
            "a.txt",
            b"a",
        )
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_missing_cdn_receipt_is_not_success():
    def handle(request):
        if request.url.path.endswith("getuploadurl"):
            return httpx.Response(200, json={"upload_param": "private"})
        return httpx.Response(200)

    with pytest.raises(WeixinError, match="UploadNotConfirmed"):
        await WeixinClient(httpx.MockTransport(handle)).upload_file(
            {"base": "https://ilinkai.weixin.qq.com", "token": "private", "peer": "p"},
            "a.txt",
            b"a",
        )
