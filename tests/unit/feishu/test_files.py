"""Native file replies: only ready deliverables, per-artifact idempotency."""

from types import SimpleNamespace

import pytest

from harness.feishu.files import MAX_FILE_REPLIES, artifact_file_replies
from harness.feishu.media import MediaRejectedError


def artifact(artifact_id: str, name: str, media_type: str, status: str = "ready"):
    return SimpleNamespace(
        artifact_id=artifact_id, name=name, media_type=media_type, status=status
    )


class Reader:
    def __init__(self) -> None:
        self.read: list[str] = []

    async def download(self, tenant_id: str, artifact_id: str):
        self.read.append(artifact_id)
        described = artifact(artifact_id, artifact_id, "application/octet-stream")
        return described, b"body:" + artifact_id.encode()


class Uploader:
    def __init__(self, *, reject: set[str] | None = None, fail: bool = False) -> None:
        self.names: list[str] = []
        self.reject = reject or set()
        self.fail = fail

    async def upload_file(self, name: str, media_type: str, content: bytes) -> str:
        self.names.append(name)
        if name in self.reject:
            raise MediaRejectedError("refused")
        if self.fail:
            raise ConnectionError("outage")
        return "file_" + content.decode().split(":", 1)[1]


@pytest.mark.asyncio
async def test_only_ready_artifacts_become_file_replies():
    reader, uploader = Reader(), Uploader()
    replies, skipped = await artifact_file_replies(
        reader,
        uploader,
        tenant_id="t",
        artifacts=[
            artifact("a1", "报告.pdf", "application/pdf"),
            artifact("a2", "草稿.md", "text/markdown", status="writing"),
            artifact("a3", "表格.xlsx", "application/vnd.ms-excel"),
            artifact("a4", "失败.html", "text/html", status="failed"),
        ],
        key_prefix="chat-result:m1",
    )
    assert [key for key, _ in replies] == ["chat-result:m1:a1", "chat-result:m1:a3"]
    assert [content.msg_type for _, content in replies] == ["file", "file"]
    assert replies[0][1].content == {"file_key": "file_a1"}
    assert skipped == []
    # A non-ready artifact is never even read.
    assert reader.read == ["a1", "a3"]


@pytest.mark.asyncio
async def test_a_rejected_file_does_not_suppress_the_others():
    reader, uploader = Reader(), Uploader(reject={"拒绝.pdf"})
    replies, skipped = await artifact_file_replies(
        reader,
        uploader,
        tenant_id="t",
        artifacts=[
            artifact("a1", "拒绝.pdf", "application/pdf"),
            artifact("a2", "ok.md", "text/markdown"),
        ],
        key_prefix="k",
    )
    assert [key for key, _ in replies] == ["k:a2"]
    assert skipped == ["a1"]


@pytest.mark.asyncio
async def test_an_outage_stops_the_batch_so_the_same_keys_are_retried():
    reader, uploader = Reader(), Uploader(fail=True)
    with pytest.raises(ConnectionError):
        await artifact_file_replies(
            reader,
            uploader,
            tenant_id="t",
            artifacts=[artifact("a1", "a.pdf", "application/pdf")],
            key_prefix="k",
        )


@pytest.mark.asyncio
async def test_the_batch_is_bounded_and_keeps_presentation_order():
    reader, uploader = Reader(), Uploader()
    artifacts = [artifact(f"a{i}", f"f{i}.txt", "text/plain") for i in range(MAX_FILE_REPLIES + 3)]
    replies, _ = await artifact_file_replies(
        reader, uploader, tenant_id="t", artifacts=artifacts, key_prefix="k"
    )
    assert len(replies) == MAX_FILE_REPLIES
    assert reader.read == [f"a{i}" for i in range(MAX_FILE_REPLIES)]


@pytest.mark.asyncio
async def test_the_limit_must_be_positive():
    with pytest.raises(ValueError):
        await artifact_file_replies(
            Reader(), Uploader(), tenant_id="t", artifacts=[], key_prefix="k", limit=0
        )
