"""Native file replies for the artifacts a finished run actually delivered.

The channel answers with text or a card today; a finished report also needs to
arrive as a file the user can open without leaving the conversation. This
module is the seam between durable artifacts and the upload transport, kept
free of the delivery loop so it can be exercised without a live bot.

Two properties matter more than the happy path:

* **Only real deliverables are offered.** An artifact the platform has not
  marked ready (still writing, failed, quarantined) must never be announced,
  or the user receives a broken file as if the work had landed.
* **Idempotency stays per artifact.** Each reply key is derived from the
  artifact id, so a retried delivery cannot duplicate a file or attach the
  wrong one; and a definitive rejection of one file does not suppress the
  others, while an outage stops the batch so the retry re-offers the same
  keys.
"""

from typing import Any, Protocol

from harness.feishu.media import MediaRejectedError
from harness.feishu.models import MessageContent, file_message

# A single turn rarely presents more than a handful of deliverables, and each
# file costs a separate platform upload; the caller's text reply still names
# everything that was produced.
MAX_FILE_REPLIES = 5


class ArtifactReader(Protocol):
    async def download(self, tenant_id: str, artifact_id: str) -> tuple[Any, bytes]: ...


class FileUploader(Protocol):
    async def upload_file(self, name: str, media_type: str, content: bytes) -> str: ...


def _status(artifact: Any) -> str:
    status = getattr(artifact, "status", "")
    return str(getattr(status, "value", status))


async def artifact_file_replies(
    reader: ArtifactReader,
    uploader: FileUploader,
    *,
    tenant_id: str,
    artifacts: list[Any],
    key_prefix: str,
    limit: int = MAX_FILE_REPLIES,
) -> tuple[list[tuple[str, MessageContent]], list[str]]:
    """Upload the ready artifacts of one turn as file replies.

    Returns the durable ``(reply_key, content)`` pairs in presentation order
    plus the ids that were definitively rejected, so the caller can say which
    files are unavailable instead of silently dropping them.
    """

    if limit < 1:
        raise ValueError("file reply limit must be positive")
    replies: list[tuple[str, MessageContent]] = []
    skipped: list[str] = []
    for artifact in artifacts:
        if len(replies) >= limit:
            break
        if _status(artifact) != "ready":
            continue
        artifact_id = str(getattr(artifact, "artifact_id", ""))
        name = str(getattr(artifact, "name", "") or "attachment")
        media_type = str(getattr(artifact, "media_type", "") or "application/octet-stream")
        current, content = await reader.download(tenant_id, artifact_id)
        try:
            key = await uploader.upload_file(name, media_type, content)
        except MediaRejectedError:
            # This file will never be accepted; the rest of the turn still ships.
            skipped.append(str(getattr(current, "artifact_id", artifact_id)))
            continue
        replies.append((f"{key_prefix}:{artifact_id}", file_message(key)))
    return replies, skipped
