import hashlib
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness.config import Settings
from harness.core.models import Artifact, ArtifactStatus
from harness.observability.provider import Observability, build_observability
from harness.runtime.artifact_tools import (
    ArtifactPublisher,
    artifact_execution_context,
    publish_artifact_tool,
)


class FakeArtifacts:
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []

    async def upload(self, **values: Any) -> Artifact:
        self.uploads.append(values)
        content = values["content"]
        return Artifact(
            artifact_id="artifact-1",
            run_id=values["run_id"],
            tenant_id=values["tenant_id"],
            name=values["name"],
            media_type=values["media_type"],
            status=ArtifactStatus.READY,
            object_key="tenant-a/artifact-1",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )


class FakeEvents:
    def __init__(self) -> None:
        self.appended: list[dict[str, Any]] = []

    async def append(self, **values: Any) -> object:
        self.appended.append(values)
        return object()


def publisher(
    workspace: Path,
    *,
    limit: int = 32,
    observability: Observability | None = None,
    validate_html: bool = False,
) -> tuple[ArtifactPublisher, FakeArtifacts, FakeEvents, list[str]]:
    artifacts = FakeArtifacts()
    events = FakeEvents()
    synced: list[str] = []

    async def sync() -> None:
        synced.append("yes")

    return (
        ArtifactPublisher(
            workspace=workspace,
            tenant_id="tenant-a",
            run_id="run-a",
            session_id="session-a",
            artifacts=artifacts,
            events=events,
            sync_workspace=sync,
            max_file_bytes=limit,
            observability=observability,
            validate_generated_html=validate_html,
        ),
        artifacts,
        events,
        synced,
    )


@pytest.mark.asyncio
async def test_publish_artifact_stores_file_then_emits_authoritative_event(
    tmp_path: Path,
) -> None:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "summary.txt").write_bytes(b"verified")
    service, artifacts, events, synced = publisher(tmp_path)

    result = await service.publish(
        path="reports/summary.txt", name="Summary", media_type="text/plain"
    )

    assert synced == ["yes"]
    assert artifacts.uploads[0]["content"] == b"verified"
    assert result.artifact_id == "artifact-1"
    assert result.name == "Summary.txt"
    assert events.appended[0]["event_type"] == "artifact.ready"
    assert events.appended[0]["payload"]["sha256"] == hashlib.sha256(b"verified").hexdigest()
    assert events.appended[0]["payload"]["source_path"] == "reports/summary.txt"


@pytest.mark.asyncio
async def test_publish_artifact_emits_stage_trace_without_file_content(
    tmp_path: Path,
) -> None:
    exporter = InMemorySpanExporter()
    observability = build_observability(
        Settings(otel_enabled=True, otlp_endpoint="http://unused/v1/traces"),
        exporter=exporter,
        processor_factory=SimpleSpanProcessor,
    )
    (tmp_path / "private.txt").write_bytes(b"private artifact body")
    service, _, _, _ = publisher(tmp_path, observability=observability)

    await service.publish(path="private.txt")

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["harness.artifact.publish"]
    assert spans[0].attributes is not None
    assert spans[0].attributes["langfuse.observation.type"] == "tool"
    assert "private artifact body" not in repr(spans)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../secret.txt", "/tmp/secret.txt", "reports"])
async def test_publish_artifact_rejects_invalid_paths(tmp_path: Path, path: str) -> None:
    (tmp_path / "reports").mkdir()
    service, artifacts, events, _ = publisher(tmp_path)

    with pytest.raises(ValueError):
        await service.publish(path=path)

    assert artifacts.uploads == []
    assert events.appended == []


@pytest.mark.asyncio
async def test_publish_artifact_rejects_missing_oversize_and_symlink_escape(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-artifact.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "large.bin").write_bytes(b"12345")
    (tmp_path / "escape.txt").symlink_to(outside)
    service, _, _, _ = publisher(tmp_path, limit=4)

    for path in ("missing.txt", "large.bin", "escape.txt"):
        with pytest.raises(ValueError):
            await service.publish(path=path)


@pytest.mark.asyncio
async def test_sdk_tool_uses_only_the_active_run_publisher(tmp_path: Path) -> None:
    (tmp_path / "answer.json").write_text('{"ok":true}', encoding="utf-8")
    service, _, _, _ = publisher(tmp_path)

    with artifact_execution_context(service):
        result = await publish_artifact_tool.handler({"path": "answer.json"})

    assert result.get("isError") is not True
    assert "artifact-1" in result["content"][0]["text"]
    with pytest.raises(RuntimeError, match="not active"):
        await publish_artifact_tool.handler({"path": "answer.json"})


@pytest.mark.asyncio
async def test_interactive_html_validation_precedes_upload_and_ready_event(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock, patch

    (tmp_path / "tool.html").write_text("<script>broken</script>")
    service, artifacts, events, _ = publisher(tmp_path, limit=200, validate_html=True)
    with patch(
        "harness.runtime.artifact_tools.validate_html_document",
        new=AsyncMock(side_effect=ValueError("invalid script")),
    ):
        with pytest.raises(ValueError, match="invalid script"):
            await service.publish(path="tool.html")
    assert artifacts.uploads == []
    assert events.appended == []


@pytest.mark.asyncio
async def test_ordinary_downloadable_html_retains_its_existing_publication_path(
    tmp_path: Path,
) -> None:
    (tmp_path / "download.html").write_text('<script src="https://example.com/lib.js"></script>')
    service, artifacts, events, _ = publisher(tmp_path, limit=200)
    await service.publish(path="download.html")
    assert len(artifacts.uploads) == 1
    assert len(events.appended) == 1
