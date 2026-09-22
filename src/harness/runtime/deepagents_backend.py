"""DeepAgents filesystem backend executed inside the platform Sandbox.

DeepAgents ships a virtual, state-backed filesystem by default. The platform
cannot use it: a Run's workspace must be a real directory it can archive,
fingerprint and publish. This backend therefore keeps DeepAgents' file semantics
but routes every operation through ``RuntimeContext.sandbox_command_executor``,
so the workspace the Agent sees is the workspace the platform owns.

Only two primitives are needed — run a command, move bytes — and both already
exist on the platform's Sandbox contract, so no new sandbox API is introduced.

Path handling: two spellings both mean "inside the Run workspace", and the model
is entitled to either. A workspace-relative path (``outputs/report.md``) is used
as given. An absolute path under the Sandbox workspace root
(``/home/user/harness/<run_id>/outputs/report.md``) has that root stripped first,
because it is what ``Bash`` reports for ``pwd`` -- a model that asks the Sandbox
where it is will use that spelling for the rest of the Run, and
``BaseSandbox`` documents absolute paths as its contract. Every other absolute
path is read as a DeepAgents *virtual* path, where ``/`` is the workspace: the
FilesystemMiddleware roots its own paths that way, and ``RunFileCapabilities``
already reads ``/workspace`` as a workspace alias. ``..`` segments are rejected
instead of resolved, so a model cannot walk out of the workspace, and a virtual
path cannot either, because its leading ``/`` is dropped rather than resolved.
``aglob`` is the exception to relative reporting: DeepAgents absolutizes its
search root, so the backend searches from the real workspace root and rebases
matches back to workspace paths.

Two contracts with the platform Sandbox are load-bearing:

* **Command shape.** Reads and writes run as
  ``python3 -c <script> <operation> <payload>``, matching the platform's own
  remote file tools, so the deferred Sandbox provider can tell a read from a
  write and synchronizes a mutated workspace back out of the Sandbox.
* **GNU grep.** DeepAgents' ``grep`` parses NUL-separated (``-Z``) output, which
  the platform's Linux Sandbox images provide. On an image whose ``grep`` lacks
  ``-Z`` the tool reports a parse error to the model instead of matching.

This module imports DeepAgents at module level and must therefore only be
imported from inside the DeepAgents runtime, never from a composition root.
"""

from __future__ import annotations

import base64
from typing import Any

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from harness.runtime.base import SandboxCommandExecutor, SandboxFilePlane
from harness.runtime.deepagents_paths import workspace_relative

# DeepAgents' own default when a caller does not pass a per-call timeout.
DEFAULT_EXECUTE_TIMEOUT_SECONDS = 300.0
# Base64 travels as argv, so uploads are chunked to stay far below ARG_MAX.
_UPLOAD_CHUNK_BYTES = 128 * 1024
# Downloads return through one bounded command result; anything larger must use
# `read_file`, which is paginated by design.
_DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024

_SYNC_UNSUPPORTED = (
    "Harness Sandbox backends are asynchronous; invoke the agent graph with "
    "astream()/ainvoke() so tool calls stay inside the Run's execution scope."
)

# The platform's remote file tools and this backend share one argv convention,
# `python3 -c <script> <operation> <payload>`, so the deferred Sandbox provider
# can tell a read from a write without understanding either script. Keeping the
# convention is what makes a Run's files synchronize back out of the Sandbox.
_WRITE_OPERATION = "write"
_READ_OPERATION = "read"

_WRITE_SCRIPT = r"""
import base64, pathlib, sys
# argv: <operation> <base64 path> <mode> [<base64 payload>]
target = pathlib.Path(base64.b64decode(sys.argv[2]).decode("utf-8"))
payload = base64.b64decode(sys.argv[4]) if len(sys.argv) > 4 else b""
target.parent.mkdir(parents=True, exist_ok=True)
with target.open(sys.argv[3]) as stream:
    stream.write(payload)
"""

_READ_SCRIPT = r"""
import base64, pathlib, sys
# argv: <operation> <base64 path>
target = pathlib.Path(base64.b64decode(sys.argv[2]).decode("utf-8"))
if not target.is_file():
    sys.stderr.write("not_found")
    sys.exit(3)
sys.stdout.write(base64.b64encode(target.read_bytes()).decode("ascii"))
"""



def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _rebase_glob(result: Any, root: str) -> Any:
    """Report glob matches as workspace paths rather than Sandbox paths.

    ``aglob`` is the one DeepAgents filesystem operation whose search root has
    to be absolute (see ``HarnessSandboxBackend._workspace_root``), so its
    matches come back carrying that root and must be stripped again before the
    model or the audit trail sees them.
    """

    matches = getattr(result, "matches", None)
    if not matches:
        return result
    prefix = f"{root}/"
    for match in matches:
        if not isinstance(match, dict):
            continue
        value = match.get("path")
        if isinstance(value, str) and value.startswith(prefix):
            match["path"] = value[len(prefix) :]
    return result


class HarnessSandboxBackend(BaseSandbox):
    """DeepAgents filesystem semantics over the platform's Sandbox primitives."""

    def __init__(
        self,
        executor: SandboxCommandExecutor,
        *,
        sandbox_id: str,
        remote_workspace: str | None = None,
        timeout_seconds: float = DEFAULT_EXECUTE_TIMEOUT_SECONDS,
        file_plane: SandboxFilePlane | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Sandbox backend timeout must be positive")
        self._executor = executor
        self._sandbox_id = sandbox_id
        self._remote_workspace = remote_workspace
        self._timeout_seconds = timeout_seconds
        self._file_plane = file_plane
        self._root: str | None = None

    @property
    def id(self) -> str:
        return self._sandbox_id

    async def _workspace_root(self) -> str:
        """Absolute path of the Run workspace inside the Sandbox, read once.

        The declared remote workspace wins because it is the same value the
        policy gate resolves model paths against, so backend and gate cannot
        disagree about where a file lives. Asking the Sandbox for its working
        directory is the fallback for a provider that declares none, and it
        returns the string ``Bash`` reports for ``pwd`` -- which is what a model
        that asks will then use.

        Every command already runs with the workspace as its working directory,
        which is why relative paths need no root at all. ``BaseSandbox.aglob``
        is the exception: it absolutizes its search root before building the
        remote command, so a relative root would turn a glob of the workspace
        into a walk of the whole Sandbox filesystem.
        """

        if self._root is None:
            if self._remote_workspace:
                self._root = self._remote_workspace.rstrip("/") or "/"
            else:
                result = await self._executor(("pwd",), None, self._timeout_seconds)
                stdout = result.stdout.strip()
                root = stdout.splitlines()[-1].strip() if stdout else ""
                if result.exit_code != 0 or not root.startswith("/"):
                    raise RuntimeError("Sandbox workspace root could not be determined")
                self._root = root.rstrip("/") or "/"
        return self._root

    async def _relative(self, value: str | None) -> str:
        """Resolve one model-supplied path into the workspace.

        Only a rooted path needs the workspace root, and reading it is cached,
        so the common workspace-relative call costs no extra Sandbox round trip.
        """

        if value is None or not value.strip().startswith("/"):
            return workspace_relative(value)
        return workspace_relative(value, root=await self._workspace_root())

    # -- platform primitives -------------------------------------------------

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        result = await self._executor(
            ("bash", "-lc", command),
            None,
            float(timeout) if timeout else self._timeout_seconds,
        )
        return ExecuteResponse(output=result.stdout + result.stderr, exit_code=result.exit_code)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse | None] = [None] * len(files)
        resolved: list[tuple[int, str, bytes]] = []
        for index, (raw_path, payload) in enumerate(files):
            try:
                relative = await self._relative(raw_path)
            except ValueError:
                responses[index] = FileUploadResponse(path=raw_path, error="invalid_path")
                continue
            resolved.append((index, relative, payload))
        if self._file_plane is None:
            for index, relative, payload in resolved:
                responses[index] = FileUploadResponse(
                    path=files[index][0], error=await self._upload(relative, payload)
                )
        else:
            # One batched write for the whole call. The command plane pays a round
            # trip and an encoded argument per file, and can only be asked for one
            # file at a time.
            error: str | None = None
            try:
                await self._file_plane.upload_files(
                    [(relative, payload) for _, relative, payload in resolved]
                )
            except Exception as upload_error:  # noqa: BLE001 - reported, not raised
                error = str(upload_error) or "upload_failed"
            for index, _, _ in resolved:
                responses[index] = FileUploadResponse(path=files[index][0], error=error)
        return [response for response in responses if response is not None]

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse | None] = [None] * len(paths)
        for index, raw_path in enumerate(paths):
            try:
                relative = await self._relative(raw_path)
            except ValueError:
                responses[index] = FileDownloadResponse(path=raw_path, error="invalid_path")
                continue
            content, error = await self._read(relative)
            responses[index] = FileDownloadResponse(
                path=raw_path, content=content, error=error
            )
        return [response for response in responses if response is not None]

    async def _upload(self, relative: str, payload: bytes) -> str | None:
        chunks = [
            payload[offset : offset + _UPLOAD_CHUNK_BYTES]
            for offset in range(0, len(payload), _UPLOAD_CHUNK_BYTES)
        ] or [b""]
        for index, chunk in enumerate(chunks):
            result = await self._executor(
                (
                    "python3",
                    "-c",
                    _WRITE_SCRIPT,
                    _WRITE_OPERATION,
                    _encoded(relative),
                    "wb" if index == 0 else "ab",
                    base64.b64encode(chunk).decode("ascii"),
                ),
                None,
                self._timeout_seconds,
            )
            if result.exit_code != 0:
                return result.stderr.strip() or "upload_failed"
        return None

    async def _read(self, relative: str) -> tuple[bytes | None, str | None]:
        """Read one file, preferring the file plane when the backend has one."""

        if self._file_plane is None:
            return await self._download(relative)
        try:
            content = await self._file_plane.download_file(
                relative, max_bytes=_DOWNLOAD_MAX_BYTES
            )
        except Exception as error:  # noqa: BLE001 - reported, not raised
            return None, str(error) or "download_failed"
        return content, None

    async def _download(self, relative: str) -> tuple[bytes | None, str | None]:
        result = await self._executor(
            ("python3", "-c", _READ_SCRIPT, _READ_OPERATION, _encoded(relative)),
            None,
            self._timeout_seconds,
        )
        if result.exit_code != 0:
            missing = "not_found" in result.stderr
            return None, "file_not_found" if missing else "download_failed"
        try:
            content = base64.b64decode(result.stdout.strip(), validate=True)
        except ValueError:
            return None, "download_failed"
        if len(content) > _DOWNLOAD_MAX_BYTES:
            return None, "file_too_large"
        return content, None

    # -- virtual -> workspace-relative paths ---------------------------------

    async def als(self, path: str) -> Any:
        return await BaseSandbox.als(self, await self._relative(path))

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        return await BaseSandbox.aread(self, await self._relative(file_path), offset, limit)

    async def awrite(self, file_path: str, content: str) -> Any:
        return await BaseSandbox.awrite(self, await self._relative(file_path), content)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> Any:
        return await BaseSandbox.aedit(
            self,
            await self._relative(file_path),
            old_string,
            new_string,
            replace_all,
        )

    async def adelete(self, file_path: str) -> Any:
        return await BaseSandbox.adelete(self, await self._relative(file_path))

    async def aglob(self, pattern: str, path: str | None = None) -> Any:
        root = await self._workspace_root()
        relative = await self._relative(path)
        search_root = root if relative == "." else f"{root}/{relative}"
        return _rebase_glob(await BaseSandbox.aglob(self, pattern, search_root), root)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        return await BaseSandbox.agrep(
            self,
            pattern,
            await self._relative(path),
            glob,
            max_count=max_count,
        )

    # BaseSandbox declares these synchronous primitives abstract. The graph is
    # always driven through `astream`, so the sync path is unreachable; failing
    # loudly beats a thread bridge that would run Sandbox commands outside the
    # Run's cancellation and timeout scope.
    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise RuntimeError(_SYNC_UNSUPPORTED)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        raise RuntimeError(_SYNC_UNSUPPORTED)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        raise RuntimeError(_SYNC_UNSUPPORTED)
