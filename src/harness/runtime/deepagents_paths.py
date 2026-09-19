"""Workspace path semantics shared by the DeepAgents backend and policy gate."""

from __future__ import annotations

# `/workspace` is the platform's own spelling of "the remote workspace": the Bash
# safety review lists it as a workspace root and `RunFileCapabilities` reads a
# path under it as workspace-relative. A provider whose remote root *is*
# `/workspace` matches through `root` and never needs this alias; one whose root
# is elsewhere needs it, or a write the gate authorized as `notes.md` would land
# in a directory literally named `workspace`.
_WORKSPACE_ALIASES: tuple[str, ...] = ("/workspace",)


def _without_workspace_root(text: str, root: str | None) -> str:
    """Drop a leading Sandbox workspace root from an absolute path."""

    for candidate in (root, *_WORKSPACE_ALIASES):
        if not candidate:
            continue
        normalized = candidate.rstrip("/")
        if normalized and (text == normalized or text.startswith(f"{normalized}/")):
            return text[len(normalized) :]
    return text


def workspace_relative(value: str | None, *, root: str | None = None) -> str:
    """Rewrite a model-supplied path into a workspace-relative one.

    ``root`` is the Sandbox workspace root, when the caller knows it. Passing it
    is what makes the two spellings of "inside the workspace" agree: without it,
    an absolute path the model read back from ``pwd`` would be read as a virtual
    path, and ``outputs/report.md`` would be written to
    ``<root>/home/user/harness/<run_id>/outputs/report.md`` -- a file the gate
    authorized under one name and the platform never looks for under another.
    """

    if value is None:
        return "."
    text = value.strip()
    if text in {"", ".", "./", "/", "./."}:
        return "."
    if any(character in text for character in ("\x00", "\n", "\r")):
        raise ValueError("workspace path contains control characters")
    if text.startswith("~"):
        raise ValueError("workspace path must not be home-relative")
    parts = [
        part
        for part in _without_workspace_root(text, root).lstrip("/").split("/")
        if part not in {"", "."}
    ]
    if ".." in parts:
        raise ValueError("workspace path must not traverse outside the workspace")
    return "/".join(parts) or "."
