"""Inline image reads from a sandboxed workspace.

A sandboxed run only exposes the MCP file tools, whose text-only read used to
turn an uploaded image into UTF-8 noise. That pushed vision-capable models into
installing OCR inside the sandbox. These tests pin the replacement contract:
images come back as image content, the server owns the size/format policy, and a
route without vision never receives image bytes.
"""

import base64
import json
import struct
import subprocess
import sys
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from harness.runtime.sandbox_tools import (
    _MAX_IMAGE_BYTES,
    _REMOTE_TOOL_SCRIPT,
    create_sandbox_tool,
)
from harness.sandbox.base import SandboxCommandResult


def _png(width: int = 4, height: int = 4) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _run_remote_read(cwd: Path, arguments: dict[str, object]) -> subprocess.CompletedProcess[str]:
    """Invoke the sandbox-side script exactly like the proxy does."""

    return subprocess.run(
        [sys.executable, "-c", _REMOTE_TOOL_SCRIPT, "read", json.dumps(arguments)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _write_image(cwd: Path, name: str = "inputs/original/photo.png") -> Path:
    path = cwd / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png())
    return path


def test_remote_read_returns_image_envelope_only_with_image_mode(tmp_path: Path) -> None:
    _write_image(tmp_path)

    plain = _run_remote_read(tmp_path, {"file_path": "inputs/original/photo.png"})
    assert plain.returncode == 0
    assert "image" not in plain.stdout

    enabled = _run_remote_read(
        tmp_path,
        {
            "file_path": "inputs/original/photo.png",
            "_image_mode": True,
            "_image_max_bytes": _MAX_IMAGE_BYTES,
        },
    )

    assert enabled.returncode == 0
    envelope = json.loads(enabled.stdout)["image"]
    assert envelope["mimeType"] == "image/png"
    assert envelope["bytes"] == len(_png())
    assert base64.b64decode(envelope["data"]) == _png()


def test_remote_read_keeps_text_files_on_the_text_path(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("first\nsecond\nthird\n")

    result = _run_remote_read(
        tmp_path,
        {"file_path": "notes.txt", "_image_mode": True, "_image_max_bytes": _MAX_IMAGE_BYTES},
    )

    assert result.returncode == 0
    assert result.stdout == "first\nsecond\nthird\n"


def test_remote_read_reports_oversized_images_instead_of_truncating(tmp_path: Path) -> None:
    path = _write_image(tmp_path)
    path.write_bytes(path.read_bytes() + b"\x00" * (1024 * 1024))

    result = _run_remote_read(
        tmp_path,
        {
            "file_path": "inputs/original/photo.png",
            "_image_mode": True,
            "_image_max_bytes": 1024,
        },
    )

    assert result.returncode != 0
    assert "returns images up to 1024 bytes" in result.stderr


@pytest.mark.asyncio
async def test_read_tool_returns_inline_image_and_forces_server_policy() -> None:
    calls: list[Sequence[str]] = []
    envelope = json.dumps(
        {"image": {"mimeType": "image/png", "bytes": 12, "data": base64.b64encode(_png()).decode()}}
    )

    async def execute(
        argv: Sequence[str],
        environment: Mapping[str, str] | None,
        timeout_seconds: float,
    ) -> SandboxCommandResult:
        calls.append(argv)
        return SandboxCommandResult(exit_code=0, stdout=envelope)

    tool = create_sandbox_tool(
        builtin="Read",
        description="test",
        schema={},
        executor=execute,
        image_aware=True,
    )

    result = await tool.handler(
        {"file_path": "inputs/original/photo.png", "_image_mode": False, "_image_max_bytes": 1}
    )

    assert [item["type"] for item in result["content"]] == ["text", "image"]
    image = result["content"][1]
    assert image["mimeType"] == "image/png"
    assert base64.b64decode(image["data"]) == _png()
    # The model cannot opt out of, or widen, the sandbox image policy.
    forwarded = json.loads(calls[0][4])
    assert forwarded["_image_mode"] is True
    assert forwarded["_image_max_bytes"] == _MAX_IMAGE_BYTES


@pytest.mark.asyncio
async def test_read_tool_stays_textual_without_vision() -> None:
    envelope = json.dumps(
        {"image": {"mimeType": "image/png", "bytes": 12, "data": base64.b64encode(_png()).decode()}}
    )

    async def execute(
        argv: Sequence[str],
        environment: Mapping[str, str] | None,
        timeout_seconds: float,
    ) -> SandboxCommandResult:
        return SandboxCommandResult(exit_code=0, stdout=envelope)

    tool = create_sandbox_tool(
        builtin="Read",
        description="test",
        schema={},
        executor=execute,
    )

    result = await tool.handler({"file_path": "inputs/original/photo.png"})

    # A route without vision never receives image bytes, even if the sandbox
    # answered with an envelope: the payload stays an opaque text result.
    assert [item["type"] for item in result["content"]] == ["text"]


@pytest.mark.asyncio
async def test_read_tool_drops_mislabelled_or_unbounded_envelopes() -> None:
    async def execute(
        argv: Sequence[str],
        environment: Mapping[str, str] | None,
        timeout_seconds: float,
    ) -> SandboxCommandResult:
        return SandboxCommandResult(
            exit_code=0,
            stdout=json.dumps(
                {
                    "image": {
                        "mimeType": "application/pdf",
                        "bytes": 4,
                        "data": "AAAA",
                    }
                }
            ),
        )

    tool = create_sandbox_tool(
        builtin="Read",
        description="test",
        schema={},
        executor=execute,
        image_aware=True,
    )

    result = await tool.handler({"file_path": "inputs/original/photo.png"})

    assert [item["type"] for item in result["content"]] == ["text"]
