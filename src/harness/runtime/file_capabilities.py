"""Run-scoped file capabilities shared by every in-process tool gate.

A tool gate needs two facts that the model must not be able to assert itself:

* whether a path a ``Write``/``Edit`` names is still inside the Run workspace;
* whether the Run itself created that file, so a later write to it is a
  continuation rather than a new capability.

Both are derived from the Run's own history, never from tool arguments, and
both are runtime-neutral: the Claude SDK hook bridge and the DeepAgents
middleware consume exactly the same bookkeeping. Claude-specific path repair
lives in ``sdk_tool_gate`` as a subclass, so this module stays free of any
single SDK's vocabulary.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from harness.runtime.base import RuntimeContext


class RunFileCapabilities:
    """Track successful, run-created files without trusting model claims."""

    def __init__(self, context: RuntimeContext) -> None:
        self._workspace = context.workspace.resolve()
        self._remote_workspace = (
            PurePosixPath(context.remote_workspace)
            if context.remote_workspace is not None
            else None
        )
        self._initial_exists: dict[Path, bool] = {}
        self._generated: set[Path] = set()
        self._pending_writes: dict[str, Path] = {}
        self._protected: set[Path] = set()
        for value in (*context.input_files, *context.processed_input_paths):
            target = self._normalize(value)
            if target is not None:
                self._protected.add(target)

    def _normalize(self, value: str) -> Path | None:
        """Resolve a model-supplied path into the Run workspace, or reject it."""

        if not value.strip():
            return None
        pure = PurePosixPath(value)
        if pure.is_absolute() and len(pure.parts) >= 2 and pure.parts[1] == "workspace":
            candidate = self._workspace.joinpath(*pure.parts[2:])
        elif (
            pure.is_absolute()
            and self._remote_workspace is not None
            and pure.is_relative_to(self._remote_workspace)
        ):
            candidate = self._workspace.joinpath(*pure.relative_to(self._remote_workspace).parts)
        else:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = self._workspace / candidate
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            return None
        if resolved == self._workspace or not resolved.is_relative_to(self._workspace):
            return None
        return resolved

    def target(self, arguments: dict[str, Any]) -> Path | None:
        value = arguments.get("file_path", arguments.get("path"))
        return self._normalize(value) if isinstance(value, str) else None

    def is_generated(self, target: Path) -> bool:
        return target in self._generated

    def generated_python_files(self) -> frozenset[str]:
        """Every spelling of the Run-created Python files, for Bash risk review."""

        values: set[str] = set()
        for target in self._generated:
            if target.suffix.lower() != ".py":
                continue
            relative = target.relative_to(self._workspace).as_posix()
            values.update(
                {
                    relative,
                    f"./{relative}",
                    target.as_posix(),
                    f"/workspace/{relative}",
                }
            )
            if self._remote_workspace is not None:
                values.add((self._remote_workspace / relative).as_posix())
        return frozenset(values)

    def observe(self, target: Path) -> None:
        self._initial_exists.setdefault(target, target.exists())

    def note_authorized_write(self, tool_call_id: str, target: Path) -> None:
        existed = self._initial_exists[target]
        relative = target.relative_to(self._workspace)
        protected = target in self._protected or (
            bool(relative.parts) and relative.parts[0] == "inputs"
        )
        if not existed and not protected:
            self._pending_writes[tool_call_id] = target

    def note_write_succeeded(self, tool_call_id: str, tool_name: str) -> None:
        target = self._pending_writes.pop(tool_call_id, None)
        if tool_name == "Write" and target is not None:
            self._generated.add(target)

    def note_write_failed(self, tool_call_id: str) -> None:
        self._pending_writes.pop(tool_call_id, None)
