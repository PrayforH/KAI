"""Run-scoped SDK tool for publishing generated workspace files."""

from __future__ import annotations

import json
import mimetypes
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server

from harness.core.models import Artifact
from harness.observability.provider import Observability
from harness.presentation.document import validate_html_document


class ArtifactUploader(Protocol):
    async def upload(
        self,
        *,
        tenant_id: str,
        run_id: str,
        name: str,
        media_type: str,
        content: bytes,
    ) -> Artifact: ...


class ArtifactEventWriter(Protocol):
    async def append(
        self,
        *,
        tenant_id: str,
        run_id: str,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> object: ...


class ArtifactPublisher:
    """Validate, durably store, then announce one workspace-relative file."""

    def __init__(
        self,
        *,
        workspace: Path,
        tenant_id: str,
        run_id: str,
        session_id: str,
        artifacts: ArtifactUploader,
        events: ArtifactEventWriter,
        sync_workspace: Callable[[], Awaitable[None]],
        max_file_bytes: int,
        observability: Observability | None = None,
        validate_generated_html: bool = False,
    ) -> None:
        if max_file_bytes < 1:
            raise ValueError("artifact size limit must be positive")
        self._workspace = workspace
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._session_id = session_id
        self._artifacts = artifacts
        self._events = events
        self._sync_workspace = sync_workspace
        self._max_file_bytes = max_file_bytes
        self._observability = observability
        self._validate_generated_html = validate_generated_html

    async def publish(
        self,
        *,
        path: str,
        name: str | None = None,
        media_type: str | None = None,
    ) -> Artifact:
        if self._observability is None:
            return await self._publish(path=path, name=name, media_type=media_type)
        with self._observability.span(
            "harness.artifact.publish",
            attributes={
                "run.id": self._run_id,
                "langfuse.observation.type": "tool",
                "langfuse.observation.metadata.tool_name": "publish_artifact",
            },
        ):
            self._observability.annotate_current_io(
                input_value={"path": path, "name": name, "media_type": media_type}
            )
            artifact = await self._publish(path=path, name=name, media_type=media_type)
            self._observability.annotate_current_io(
                output_value={
                    "artifact_id": artifact.artifact_id,
                    "name": artifact.name,
                    "media_type": artifact.media_type,
                }
            )
            return artifact

    async def _publish(
        self,
        *,
        path: str,
        name: str | None,
        media_type: str | None,
    ) -> Artifact:
        relative = PurePosixPath(path)
        if not path.strip() or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("artifact path must stay within the run workspace")

        await self._sync_workspace()
        root = self._workspace.resolve()
        candidate = self._workspace.joinpath(*relative.parts)
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, RuntimeError):
            raise ValueError("artifact file does not exist") from None
        if not resolved.is_relative_to(root):
            raise ValueError("artifact path escaped the run workspace")
        if not resolved.is_file():
            raise ValueError("artifact path must reference a file")
        size = resolved.stat().st_size
        if size > self._max_file_bytes:
            raise ValueError(f"artifact exceeds maximum size of {self._max_file_bytes} bytes")

        display_name = name.strip() if isinstance(name, str) else ""
        source_suffix = PurePosixPath(resolved.name).suffix
        if display_name and source_suffix and not PurePosixPath(display_name).suffix:
            display_name = f"{display_name}{source_suffix}"
        content = resolved.read_bytes()
        content_type = (
            media_type or mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        )
        if (
            self._validate_generated_html
            and content_type.split(";", 1)[0].strip().lower() == "text/html"
        ):
            await validate_html_document(content)
        artifact = await self._artifacts.upload(
            tenant_id=self._tenant_id,
            run_id=self._run_id,
            name=display_name or resolved.name,
            media_type=content_type,
            content=content,
        )
        payload = artifact.model_dump(mode="json")
        payload["source_path"] = relative.as_posix()
        await self._events.append(
            tenant_id=self._tenant_id,
            run_id=self._run_id,
            session_id=self._session_id,
            event_type="artifact.ready",
            payload=payload,
        )
        return artifact


_artifact_publisher: ContextVar[ArtifactPublisher | None] = ContextVar(
    "harness_artifact_publisher", default=None
)


@contextmanager
def artifact_execution_context(publisher: ArtifactPublisher) -> Generator[None]:
    token = _artifact_publisher.set(publisher)
    try:
        yield
    finally:
        _artifact_publisher.reset(token)


async def _publish_artifact(arguments: dict[str, Any]) -> dict[str, Any]:
    publisher = _artifact_publisher.get()
    if publisher is None:
        raise RuntimeError("artifact execution context is not active")
    path = arguments.get("path")
    name = arguments.get("name")
    media_type = arguments.get("media_type")
    if not isinstance(path, str):
        return _tool_error("path must be a workspace-relative string")
    if name is not None and not isinstance(name, str):
        return _tool_error("name must be a string")
    if media_type is not None and not isinstance(media_type, str):
        return _tool_error("media_type must be a string")
    try:
        artifact = await publisher.publish(
            path=path,
            name=name,
            media_type=media_type,
        )
    except ValueError as error:
        return _tool_error(str(error))
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(artifact.model_dump(mode="json"), separators=(",", ":")),
            }
        ]
    }


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


publish_artifact_tool = SdkMcpTool(
    name="publish_artifact",
    description=(
        "Publish a generated file from the current run workspace as a durable "
        "Harness artifact for the user to preview or download."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "name": {"type": "string"},
            "media_type": {"type": "string"},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    handler=_publish_artifact,
)


def create_artifact_mcp_server() -> McpSdkServerConfig:
    return create_sdk_mcp_server("harness-artifacts", tools=[publish_artifact_tool])
