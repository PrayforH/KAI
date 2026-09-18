"""Claude SDK MCP tools that execute filesystem and shell work in a Sandbox."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server

from harness.core.manifest import PythonToolSnapshot
from harness.runtime.base import SandboxCommandExecutor

SERVER_NAME = "harness-sandbox"
SUPPORTED_BUILTINS = frozenset({"Read", "Write", "Edit", "Bash", "Glob", "Grep"})
COORDINATION_BUILTINS = frozenset({"Task", "Agent"})
_MAX_ARGUMENT_CHARS = 512 * 1024
_MAX_OUTPUT_CHARS = 256 * 1024
# Image bytes travel back through the tool-result channel, so the payload must
# fit the CLI's JSON buffer (32 MiB) together with base64 expansion and JSON
# escaping. Anything larger fails with an explicit message instead of a
# truncated frame that would reach the model as a corrupt image.
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_IMAGE_MIMES = ("image/png", "image/jpeg", "image/webp", "image/gif")

_BUNDLE_PYTHON_RUNNER = r"""
import asyncio
import importlib.util
import inspect
import json
import sys
from pathlib import Path

root = Path.cwd().resolve()
path = (root / sys.argv[1]).resolve()
if path == root or root not in path.parents or path.suffix != ".py":
    raise ValueError("Bundle tool path escaped workspace")
spec = importlib.util.spec_from_file_location("harness_bundle_tool", path)
if spec is None or spec.loader is None:
    raise ValueError("Bundle tool could not be loaded")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
handler = getattr(module, "run", None)
if not callable(handler):
    raise ValueError("Bundle tool has no callable run(arguments)")
value = handler(json.loads(sys.argv[2]))
if inspect.isawaitable(value):
    value = asyncio.run(value)
print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
"""

_REMOTE_TOOL_SCRIPT = r"""
import fnmatch
import json
import re
import sys
from pathlib import Path

operation = sys.argv[1]
arguments = json.loads(sys.argv[2])
root = Path.cwd().resolve()

def target(value):
    candidate = Path(value)
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("path escaped workspace")
    return resolved

def image_mime(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None

if operation == "read":
    path = target(arguments["file_path"])
    image_limit = arguments.get("_image_max_bytes")
    if arguments.get("_image_mode") is True and isinstance(image_limit, int) and path.is_file():
        size = path.stat().st_size
        if size > image_limit:
            raise ValueError(
                "image is %d bytes; this session returns images up to %d bytes. "
                "Downscale it or read a processed representation instead."
                % (size, image_limit)
            )
        data = path.read_bytes()
        mime = image_mime(data)
        if mime is not None:
            import base64
            print(json.dumps({
                "image": {
                    "mimeType": mime,
                    "bytes": len(data),
                    "data": base64.b64encode(data).decode("ascii"),
                }
            }))
            sys.exit(0)
    offset = max(0, int(arguments.get("offset", 0)))
    limit = max(1, min(10000, int(arguments.get("limit", 2000))))
    lines = path.read_text(errors="replace").splitlines()
    print("\n".join(lines[offset:offset + limit]))
elif operation == "write":
    path = target(arguments["file_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    content = arguments["content"]
    path.write_text(content)
    print(json.dumps({"path": str(path.relative_to(root)), "bytes": len(content.encode())}))
elif operation == "edit":
    path = target(arguments["file_path"])
    content = path.read_text()
    old = arguments["old_string"]
    new = arguments["new_string"]
    count = content.count(old)
    if count == 0:
        raise ValueError("old_string was not found")
    if count > 1 and not arguments.get("replace_all", False):
        raise ValueError("old_string is not unique; set replace_all=true")
    updated = (
        content.replace(old, new)
        if arguments.get("replace_all", False)
        else content.replace(old, new, 1)
    )
    path.write_text(updated)
    replacements = count if arguments.get("replace_all", False) else 1
    print(json.dumps({
        "path": str(path.relative_to(root)),
        "replacements": replacements,
    }))
elif operation == "glob":
    base = target(arguments.get("path", "."))
    pattern = arguments["pattern"]
    matches = sorted(
        str(path.relative_to(root))
        for path in base.rglob("*")
        if path.is_file() and fnmatch.fnmatch(str(path.relative_to(base)), pattern)
    )
    print("\n".join(matches[:1000]))
elif operation == "grep":
    base = target(arguments.get("path", "."))
    flags = re.IGNORECASE if arguments.get("case_insensitive") else 0
    expression = re.compile(arguments["pattern"], flags)
    file_glob = arguments.get("glob", "*")
    output_mode = arguments.get("output_mode", "files_with_matches")
    limit = max(1, min(2000, int(arguments.get("head_limit", 200))))
    output = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or not fnmatch.fnmatch(path.name, file_glob):
            continue
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        found = [(index, line) for index, line in enumerate(lines, 1) if expression.search(line)]
        if not found:
            continue
        relative = str(path.relative_to(root))
        if output_mode == "count":
            output.append(f"{relative}:{len(found)}")
        elif output_mode == "content":
            output.extend(f"{relative}:{index}:{line}" for index, line in found)
        else:
            output.append(relative)
        if len(output) >= limit:
            break
    print("\n".join(output[:limit]))
else:
    raise ValueError("unsupported sandbox tool")
"""


def proxy_tool_name(builtin: str) -> str:
    if builtin not in SUPPORTED_BUILTINS:
        raise ValueError(f"unsupported deferred sandbox builtin: {builtin}")
    return f"mcp__{SERVER_NAME}__{builtin.lower()}"


def canonical_tool_name(name: str) -> str:
    web_names = {"mcp__harness-web__search": "WebSearch", "mcp__harness-web__fetch": "WebFetch"}
    if name in web_names:
        return web_names[name]
    prefix = f"mcp__{SERVER_NAME}__"
    if not name.startswith(prefix):
        return name
    candidate = name.removeprefix(prefix)
    return next(
        (builtin for builtin in SUPPORTED_BUILTINS if builtin.lower() == candidate),
        name,
    )


def _tool_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text[:_MAX_OUTPUT_CHARS]}],
        **({"isError": True} if is_error else {}),
    }


def _image_tool_result(stdout: str) -> dict[str, Any] | None:
    """Return an inline image result when the sandbox reported image bytes.

    The sandbox decides whether a path is an allowed image by sniffing its magic
    bytes; this side re-validates the envelope so a hostile or corrupt sandbox
    reply cannot smuggle an unbounded or mislabelled payload into the model
    request.
    """

    base64_budget = (4 * _MAX_IMAGE_BYTES // 3) + 64
    if len(stdout) > base64_budget + 4096:
        return None
    try:
        parsed = json.loads(stdout)
    except ValueError:
        return None
    image = parsed.get("image") if isinstance(parsed, dict) else None
    if not isinstance(image, dict):
        return None
    data = image.get("data")
    mime = image.get("mimeType")
    size = image.get("bytes")
    if not isinstance(data, str) or mime not in _IMAGE_MIMES or not data:
        return None
    if len(data) > base64_budget:
        return None
    described = f"{size} bytes" if isinstance(size, int) else "unknown size"
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Image returned inline ({mime}, {described}). Answer from the image "
                    "itself; do not install OCR or image libraries."
                ),
            },
            {"type": "image", "data": data, "mimeType": mime},
        ]
    }


def create_sandbox_tool(
    *,
    builtin: str,
    description: str,
    schema: dict[str, Any],
    executor: SandboxCommandExecutor,
    image_aware: bool = False,
) -> SdkMcpTool[Any]:
    operation = builtin.lower()
    returns_images = image_aware and builtin == "Read"

    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        payload = dict(arguments)
        if returns_images:
            # Server-owned flags: the model cannot widen, disable or resize the
            # image path, whatever it puts in its tool arguments.
            payload["_image_mode"] = True
            payload["_image_max_bytes"] = _MAX_IMAGE_BYTES
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > _MAX_ARGUMENT_CHARS:
            return _tool_result("tool arguments exceed the sandbox proxy limit", is_error=True)
        timeout = 30.0
        if builtin == "Bash":
            raw_timeout = arguments.get("timeout")
            if isinstance(raw_timeout, (int, float)) and not isinstance(raw_timeout, bool):
                timeout = max(1.0, min(120.0, float(raw_timeout) / 1000))
            command = arguments.get("command")
            if not isinstance(command, str) or not command.strip():
                return _tool_result("command must be a non-empty string", is_error=True)
            result = await executor(("bash", "-lc", command), None, timeout)
        else:
            result = await executor(
                ("python3", "-c", _REMOTE_TOOL_SCRIPT, operation, encoded),
                None,
                timeout,
            )
        if result.exit_code != 0:
            message = result.stderr.strip() or result.stdout.strip() or "sandbox tool failed"
            return _tool_result(message, is_error=True)
        if returns_images:
            image_result = _image_tool_result(result.stdout)
            if image_result is not None:
                return image_result
        return _tool_result(result.stdout)

    return SdkMcpTool(
        name=builtin.lower(),
        description=description,
        input_schema=schema,
        handler=handler,
    )


def create_sandbox_tools_mcp_server(
    executor: SandboxCommandExecutor,
    builtins: Iterable[str],
    *,
    image_aware: bool = False,
) -> McpSdkServerConfig:
    """Proxy workspace builtins into the sandbox.

    ``image_aware`` enables inline image reads: when the selected model route
    supports vision, ``Read`` returns allowed image formats as image content
    instead of UTF-8 noise, so a sandboxed run never has to fall back to OCR.
    """

    requested = frozenset(builtins)
    unsupported = requested - SUPPORTED_BUILTINS
    if unsupported:
        raise ValueError(
            "unsupported deferred sandbox builtins: " + ", ".join(sorted(unsupported))
        )
    definitions = {
        "Read": (
            (
                "Read a UTF-8 text file, or an image, from the isolated workspace. "
                "Reading a PNG, JPEG, WebP or GIF returns the image content itself; "
                "answer from the returned image instead of installing OCR or image "
                "libraries."
            )
            if image_aware
            else "Read a UTF-8 text file from the isolated workspace.",
            {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
        ),
        "Write": (
            "Write a UTF-8 file inside the isolated workspace.",
            {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
                "additionalProperties": False,
            },
        ),
        "Edit": (
            "Replace exact text in a file inside the isolated workspace.",
            {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["file_path", "old_string", "new_string"],
                "additionalProperties": False,
            },
        ),
        "Bash": (
            "Run a shell command inside the isolated workspace.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "number", "minimum": 1000, "maximum": 120000},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        ),
        "Glob": (
            "Find files matching a glob in the isolated workspace.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        ),
        "Grep": (
            "Search text files with a regular expression in the isolated workspace.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files_with_matches", "count"],
                    },
                    "case_insensitive": {"type": "boolean"},
                    "head_limit": {"type": "integer", "minimum": 1},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        ),
    }
    tools = [
        create_sandbox_tool(
            builtin=builtin,
            description=definitions[builtin][0],
            schema=definitions[builtin][1],
            executor=executor,
            image_aware=image_aware,
        )
        for builtin in sorted(requested)
    ]
    return create_sdk_mcp_server(SERVER_NAME, tools=tools)


def create_bundle_python_tools_mcp_server(
    *,
    server_name: str,
    snapshots: Iterable[PythonToolSnapshot],
    materialized_paths: Mapping[str, Path],
    executor: SandboxCommandExecutor,
) -> McpSdkServerConfig:
    """Expose self-contained Bundle operators while executing code in the Sandbox."""

    tools = [
        create_bundle_python_tool(
            snapshot=snapshot,
            materialized_path=materialized_paths.get(snapshot.reference),
            executor=executor,
        )
        for snapshot in snapshots
    ]
    return create_sdk_mcp_server(server_name, tools=tools)


def create_bundle_python_tool(
    *,
    snapshot: PythonToolSnapshot,
    materialized_path: Path | None,
    executor: SandboxCommandExecutor,
) -> SdkMcpTool[Any]:
    if (
        materialized_path is None
        or materialized_path.is_absolute()
        or ".." in materialized_path.parts
    ):
        raise ValueError(f"Bundle tool was not materialized: {snapshot.reference}")

    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > _MAX_ARGUMENT_CHARS:
            return _tool_result(
                "tool arguments exceed the Bundle operator limit",
                is_error=True,
            )
        result = await executor(
            (
                "python3",
                "-c",
                _BUNDLE_PYTHON_RUNNER,
                materialized_path.as_posix(),
                encoded,
            ),
            None,
            120.0,
        )
        if result.exit_code != 0:
            message = result.stderr.strip() or result.stdout.strip() or "Bundle tool failed"
            return _tool_result(message, is_error=True)
        return _tool_result(result.stdout)

    return SdkMcpTool(
        name=snapshot.name,
        description=snapshot.description,
        input_schema=snapshot.input_schema,
        handler=handler,
    )
