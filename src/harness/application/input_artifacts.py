"""Pre-run input artifact lifecycle and ownership rules."""

import asyncio
import re
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from harness.application.file_catalog import FileCatalogService
from harness.application.types import Clock, IdGenerator
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import (
    ArtifactStatus,
    ExecutionIdentity,
    InputArtifact,
    ProcessingStatus,
)
from harness.core.ports import ArtifactStore, InputArtifactRepository
from harness.inputs.base import InputProcessingResult, InputProcessor


def _replace_read_only_file(target: Path, content: bytes) -> None:
    """Atomically restage an immutable input restored from a prior snapshot."""

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.chmod(0o444)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


class StagedInputArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_artifact_id: str
    name: str
    media_type: str
    size_bytes: int
    path: str
    processed_paths: tuple[str, ...] = Field(default=(), exclude=True)


class InputArtifactService:
    def __init__(
        self,
        *,
        repository: InputArtifactRepository,
        store: ArtifactStore,
        id_generator: IdGenerator,
        clock: Clock,
        max_file_bytes: int = 50 * 1024 * 1024,
        max_files_per_run: int = 10,
        max_total_bytes: int = 100 * 1024 * 1024,
        processor: InputProcessor | None = None,
        file_catalog: FileCatalogService | None = None,
    ) -> None:
        self._repository = repository
        self._store = store
        self._id_generator = id_generator
        self._clock = clock
        self.max_file_bytes = max_file_bytes
        self.max_files_per_run = max_files_per_run
        self.max_total_bytes = max_total_bytes
        self._processor = processor
        self._file_catalog = file_catalog

    async def upload(
        self,
        *,
        tenant_id: str,
        user_id: str,
        name: str,
        media_type: str,
        content: bytes,
    ) -> InputArtifact:
        if len(content) > self.max_file_bytes:
            raise ConflictError(
                f"input artifact exceeds maximum size of {self.max_file_bytes} bytes"
            )
        input_artifact_id = self._id_generator("input_artifact")
        pending = InputArtifact(
            input_artifact_id=input_artifact_id,
            tenant_id=tenant_id,
            user_id=user_id,
            name=name,
            media_type=media_type,
            status=ArtifactStatus.PENDING,
            object_key=f".pending/{tenant_id}/{input_artifact_id}",
            created_at=self._clock(),
        )
        await self._repository.add(pending)
        try:
            stored = await self._store.put(tenant_id, input_artifact_id, content)
        except Exception:
            await self._repository.update(
                pending.model_copy(update={"status": ArtifactStatus.FAILED})
            )
            raise
        ready = pending.model_copy(
            update={
                "status": ArtifactStatus.READY,
                "object_key": stored.object_key,
                "sha256": stored.sha256,
                "size_bytes": stored.size_bytes,
            }
        )
        await self._repository.update(ready)
        return ready

    async def resolve_for_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        input_artifact_ids: Sequence[str],
    ) -> list[InputArtifact]:
        unique_ids = list(dict.fromkeys(input_artifact_ids))
        if len(unique_ids) > self.max_files_per_run:
            raise ConflictError(
                f"a run accepts at most {self.max_files_per_run} input artifacts"
            )

        resolved: list[InputArtifact] = []
        total_size = 0
        for input_artifact_id in unique_ids:
            artifact = await self._repository.get(tenant_id, input_artifact_id)
            if artifact.user_id != user_id or artifact.status is not ArtifactStatus.READY:
                raise NotFoundError(f"input artifact not found: {input_artifact_id}")
            total_size += artifact.size_bytes or 0
            resolved.append(artifact)

        if total_size > self.max_total_bytes:
            raise ConflictError(
                f"input artifact total size exceeds {self.max_total_bytes} bytes"
            )
        return resolved

    async def download(
        self,
        *,
        tenant_id: str,
        user_id: str,
        input_artifact_id: str,
    ) -> tuple[InputArtifact, bytes]:
        artifact = await self._repository.get(tenant_id, input_artifact_id)
        if artifact.user_id != user_id or artifact.status is not ArtifactStatus.READY:
            raise NotFoundError(f"input artifact not found: {input_artifact_id}")
        return artifact, await self._store.get(tenant_id, input_artifact_id)

    async def stage_for_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        input_artifact_ids: Sequence[str],
        workspace: Path,
        identity: ExecutionIdentity | None = None,
    ) -> list[StagedInputArtifact]:
        artifacts = await self.resolve_for_run(
            tenant_id=tenant_id,
            user_id=user_id,
            input_artifact_ids=input_artifact_ids,
        )
        inputs_root = workspace / "inputs"
        await asyncio.to_thread(inputs_root.mkdir, parents=True, exist_ok=True)
        staged: list[StagedInputArtifact] = []
        for artifact in artifacts:
            content = await self._store.get(tenant_id, artifact.input_artifact_id)
            name = _safe_filename(artifact.name)
            stable_id = re.sub(r"[^A-Za-z0-9_-]", "_", artifact.input_artifact_id)[-32:]
            if self._processor is None:
                target = inputs_root / f"{stable_id}-{name}"
            else:
                target = inputs_root / "original" / f"{stable_id}-{name}"
                await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            root = workspace.resolve()
            if not target.resolve().is_relative_to(root):
                raise ValueError("input artifact path escaped the workspace")
            await asyncio.to_thread(_replace_read_only_file, target, content)
            processed_paths: list[str] = []
            if self._processor is not None:
                if identity is None or self._file_catalog is None:
                    raise RuntimeError(
                        "input identity and file catalog are required for processing"
                    )
                try:
                    result = await asyncio.to_thread(
                        self._processor.process,
                        name=name,
                        media_type=artifact.media_type,
                        content=content,
                    )
                except Exception as error:
                    result = InputProcessingResult(
                        status=ProcessingStatus.FAILED,
                        processor="failed",
                        error_code=type(error).__name__,
                    )
                original = await self._file_catalog.record_original(
                    identity=identity,
                    input_artifact_id=artifact.input_artifact_id,
                    name=name,
                    media_type=artifact.media_type,
                    path=target.relative_to(workspace).as_posix(),
                    metadata={
                        "processing_status": result.status.value,
                        "processor": result.processor,
                        "processing_error_code": result.error_code,
                        **result.metadata,
                    },
                )
                processed_root = inputs_root / "processed" / stable_id
                for derived in result.derived:
                    relative = Path(derived.relative_path)
                    if relative.is_absolute() or ".." in relative.parts:
                        raise ValueError("processed input path escaped its root")
                    derived_target = processed_root / relative
                    if not derived_target.resolve().is_relative_to(root):
                        raise ValueError("processed input path escaped the workspace")
                    await asyncio.to_thread(
                        _replace_read_only_file, derived_target, derived.content
                    )
                    derived_path = derived_target.relative_to(workspace).as_posix()
                    processed_paths.append(derived_path)
                    await self._file_catalog.record_derived(
                        identity=identity,
                        parent=original,
                        name=relative.name,
                        media_type=derived.media_type,
                        path=derived_path,
                        metadata=derived.metadata,
                    )
            staged.append(
                StagedInputArtifact(
                    input_artifact_id=artifact.input_artifact_id,
                    name=name,
                    media_type=artifact.media_type,
                    size_bytes=len(content),
                    path=target.relative_to(workspace).as_posix(),
                    processed_paths=tuple(processed_paths),
                )
            )
        return staged


def _safe_filename(name: str) -> str:
    basename = Path(name.replace("\\", "/")).name
    sanitized = re.sub(r"[^\w. -]", "_", basename, flags=re.UNICODE)
    sanitized = sanitized.strip(" .")[:120]
    return sanitized or "input"
