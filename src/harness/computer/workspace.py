"""Bounded file transfer through the same unprivileged execution lease."""

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from harness.computer import workspace_guest
from harness.computer.docker import DockerComputerDriver
from harness.computer.manager import ComputerAllocation
from harness.sandbox.base import replace_collected_file, workspace_relative_target

_GUEST = Path(workspace_guest.__file__).read_text()


class WorkspaceFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0, strict=True)
    mode: int = Field(ge=0, le=0o777, strict=True)
    signature: tuple[int, int, int, int, int]


class ComputerWorkspace:
    def __init__(
        self,
        driver: DockerComputerDriver,
        allocation: ComputerAllocation,
        *,
        max_bytes: int,
        max_members: int,
    ) -> None:
        if min(max_bytes, max_members) < 1:
            raise ValueError("computer workspace limits must be positive")
        self.driver, self.allocation = driver, allocation
        self.max_bytes, self.max_members = max_bytes, max_members
        self.baseline: dict[str, WorkspaceFile] | None = None
        self._collected: set[str] = set()

    async def _call(self, operation: str, payload: Mapping[str, object]) -> dict[str, Any]:
        result = await self.driver.execute(
            self.allocation,
            ["python3", "-I", "-c", _GUEST, operation],
            stdin=json.dumps(payload).encode(),
            timeout_seconds=120,
        )
        if result.exit_code:
            raise RuntimeError("Computer workspace operation could not be confirmed")
        try:
            value = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError("Computer workspace response is invalid") from None
        if not isinstance(value, dict):
            raise RuntimeError("Computer workspace response is invalid")
        return cast(dict[str, Any], value)

    async def manifest(self) -> dict[str, WorkspaceFile]:
        result = await self._call(
            "manifest", {"max_bytes": self.max_bytes, "max_members": self.max_members}
        )
        files = {path: WorkspaceFile.model_validate(info) for path, info in result.items()}
        if (
            len(files) > self.max_members
            or sum(info.size for info in files.values()) > self.max_bytes
        ):
            raise ValueError("Computer workspace manifest exceeds limits")
        for path in files:
            workspace_relative_target("/home/kai/workspace", path)
        return files

    async def capture_baseline(self) -> None:
        if self.baseline is not None:
            raise RuntimeError("Computer workspace baseline is already captured")
        self.baseline = await self.manifest()

    def fingerprints(self) -> dict[str, str]:
        return {path: item.sha256 for path, item in (self.baseline or {}).items()}

    async def upload(self, entries: Sequence[tuple[str, bytes, int]]) -> None:
        total = sum(len(data) for _, data, _ in entries)
        if total > self.max_bytes or len(entries) > self.max_members:
            raise ValueError("Computer workspace upload exceeds limits")
        for path, _, _ in entries:
            workspace_relative_target("/home/kai/workspace", path)
        batch: list[tuple[str, bytes, int]] = []
        batch_bytes = 0
        for path, data, mode in entries:
            if batch and (
                batch_bytes + len(data) > workspace_guest.CHUNK_BYTES
                or len(batch) >= workspace_guest.BATCH_FILES
            ):
                await self._upload_batch(batch)
                batch, batch_bytes = [], 0
            if len(data) <= workspace_guest.CHUNK_BYTES:
                batch.append((path, data, mode))
                batch_bytes += len(data)
            else:
                await self._upload_chunks(path, data, mode)
        if batch:
            await self._upload_batch(batch)

    async def _upload_batch(self, entries: Sequence[tuple[str, bytes, int]]) -> None:
        result = await self._call(
            "upload_batch",
            {
                "entries": [
                    {
                        "path": path,
                        "upload_id": uuid4().hex,
                        "offset": 0,
                        "size": len(data),
                        "mode": mode,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "data": base64.b64encode(data).decode("ascii"),
                    }
                    for path, data, mode in entries
                ]
            },
        )
        if result.get("complete") is not True or result.get("count") != len(entries):
            raise RuntimeError("Computer workspace batch acknowledgement is invalid")

    async def _upload_chunks(self, path: str, data: bytes, mode: int) -> None:
        identifier = uuid4().hex
        digest = hashlib.sha256(data).hexdigest()
        try:
            for offset in range(0, max(1, len(data)), workspace_guest.CHUNK_BYTES):
                chunk = data[offset : offset + workspace_guest.CHUNK_BYTES]
                result = await self._call(
                    "upload",
                    {
                        "path": path,
                        "upload_id": identifier,
                        "offset": offset,
                        "size": len(data),
                        "mode": mode,
                        "sha256": digest,
                        "data": base64.b64encode(chunk).decode("ascii"),
                    },
                )
                if result.get("complete") is not (offset + len(chunk) == len(data)):
                    raise RuntimeError("Computer workspace upload acknowledgement is invalid")
        except BaseException:
            try:
                await self._call("abort", {"path": path, "upload_id": identifier})
            except Exception:
                # A lost lease must never be bypassed to clean up a user's
                # temporary upload; it is outside manifest/publication.
                pass
            raise

    async def stage(self, local: Path) -> None:
        """Only stage this fresh Run directory; never a prior Session snapshot."""
        entries: list[tuple[str, bytes, int]] = []
        size = 0
        for path in local.rglob("*"):
            # SDK control configuration belongs to the Worker. In particular,
            # its gateway tokens must not be copied to the owner's work disk.
            if path.relative_to(local).parts[0] in {".harness-runtime", ".tmp", ".git"}:
                continue
            if path.is_symlink():
                raise ValueError("Computer input staging refuses symbolic links")
            if not path.is_file():
                continue
            size += path.stat().st_size
            if size > self.max_bytes or len(entries) >= self.max_members:
                raise ValueError("Computer input staging exceeds limits")
            entries.append(
                (path.relative_to(local).as_posix(), path.read_bytes(), path.stat().st_mode & 0o777)
            )
        await self.upload(entries)

    async def download(
        self, path: str, *, max_bytes: int, metadata: WorkspaceFile | None = None
    ) -> bytes:
        workspace_relative_target("/home/kai/workspace", path)
        if max_bytes < 0:
            raise ValueError("Computer workspace download bound is invalid")
        info = metadata or WorkspaceFile.model_validate(
            await self._call("stat", {"path": path, "max_bytes": max_bytes})
        )
        size = info.size
        if not 0 <= size <= min(max_bytes, self.max_bytes):
            raise ValueError("Computer workspace file exceeds download limit")
        content = bytearray()
        for offset in range(0, size, workspace_guest.CHUNK_BYTES):
            value = await self._call(
                "read",
                {
                    "path": path,
                    "offset": offset,
                    "length": min(workspace_guest.CHUNK_BYTES, size - offset),
                    "signature": info.signature,
                },
            )
            data = base64.b64decode(value["data"], validate=True)
            if len(data) != min(workspace_guest.CHUNK_BYTES, size - offset):
                raise RuntimeError("Computer workspace download was truncated")
            content.extend(data)
        if hashlib.sha256(content).hexdigest() != info.sha256:
            raise RuntimeError("Computer workspace file changed during download")
        return bytes(content)

    @staticmethod
    def mirror_target(local: Path, path: str) -> Path:
        workspace_relative_target("/home/kai/workspace", path)
        target = local.joinpath(*path.split("/"))
        if target.is_symlink() or not target.resolve().is_relative_to(local.resolve()):
            raise ValueError("Computer collection path escaped its local mirror")
        return target

    async def collect(self, local: Path) -> None:
        if self.baseline is None:
            raise RuntimeError("Computer collection requires an initial baseline")
        current = await self.manifest()
        changed = {
            path: info
            for path, info in current.items()
            if (path not in self.baseline or self.baseline[path].sha256 != info.sha256)
        }
        if sum(info.size for info in changed.values()) > self.max_bytes:
            raise ValueError("Computer changed files exceed collection limit")
        # Remove only copies collected earlier in this execution. The remote
        # disk is never modified by collection, and fresh staged inputs stay.
        for path in self._collected - set(changed):
            self.mirror_target(local, path).unlink(missing_ok=True)
        for path, info in changed.items():
            content = await self.download(path, max_bytes=self.max_bytes, metadata=info)
            replace_collected_file(self.mirror_target(local, path), content, mode=info.mode)
        self._collected = set(changed)
