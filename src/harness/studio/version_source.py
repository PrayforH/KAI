"""Bounded, comparable files from immutable Agent publication snapshots."""

import base64
import hashlib
import json

import yaml

from harness.core.manifest import AgentManifestSnapshot
from harness.studio.deepagents_export import (
    PROJECT_SOURCE_FILE_LIMIT,
    PROJECT_SOURCE_TOTAL_LIMIT,
    DeepagentsProjectSource,
    ProjectSourceFile,
)
from harness.studio.models import AgentDraft


class _SourceFiles:
    def __init__(self) -> None:
        self.files: list[ProjectSourceFile] = []
        self.remaining = PROJECT_SOURCE_TOTAL_LIMIT

    def add(self, path: str, raw: bytes) -> None:
        text = None
        unavailable = None
        if len(raw) > min(PROJECT_SOURCE_FILE_LIMIT, self.remaining):
            unavailable = "文件超出预览大小限制。"
        else:
            try:
                text = raw.decode("utf-8")
                if "\x00" in text:
                    raise UnicodeError
                self.remaining -= len(raw)
            except UnicodeError:
                text = None
                unavailable = "二进制文件，可根据内容校验识别变化。"
        self.files.append(ProjectSourceFile(
            path=path, content=text, size=len(raw),
            digest=hashlib.sha256(raw).hexdigest(), unavailable=unavailable,
        ))


def version_source(
    snapshot: AgentManifestSnapshot, *, revision: int = 0
) -> DeepagentsProjectSource:
    source = _SourceFiles()
    add = source.add
    manifest = snapshot.manifest.model_dump(mode="json", by_alias=True)
    # Version is shown in the comparison header; a version bump alone is not a
    # configuration change. Preserve all other metadata and runtime settings.
    manifest["metadata"].pop("version", None)
    add(
        "agent.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )
    add("AGENTS.md", snapshot.system_prompt.encode())
    for skill in sorted(snapshot.skill_snapshots, key=lambda skill: skill.name):
        for file in sorted(skill.files, key=lambda file: file.path):
            add(
                f"skills/{skill.name}/{file.path}",
                base64.b64decode(file.content_base64, validate=True),
            )
    for tool in sorted(snapshot.python_tool_snapshots, key=lambda tool: tool.path):
        add(tool.path, base64.b64decode(tool.content_base64, validate=True))
    if snapshot.tool_directory is not None:
        add(
            "tool-directory.json",
            (
                json.dumps(
                    snapshot.tool_directory.model_dump(mode="json", by_alias=True),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode(),
        )
    return DeepagentsProjectSource(
        revision=revision,
        filename=f"{snapshot.manifest.metadata.name}@{snapshot.manifest.metadata.version}",
        digest=snapshot.content_hash,
        framework_version="",
        files=tuple(source.files),
    )


def draft_version_source(draft: AgentDraft) -> DeepagentsProjectSource:
    """Display saved draft data even when that revision cannot currently compile."""
    source = _SourceFiles()
    spec = draft.spec.model_dump(mode="json", by_alias=True,
                                 exclude={"system_prompt", "skills", "python_tools"})
    spec["skills"] = [{"name": s.name, "description": s.description} for s in draft.spec.skills]
    spec["pythonTools"] = [t.model_dump(mode="json", by_alias=True, exclude={"code"})
                           for t in draft.spec.python_tools]
    source.add("draft.json", (json.dumps(spec, ensure_ascii=False, indent=2,
                                        sort_keys=True) + "\n").encode())
    source.add("AGENTS.md", draft.spec.system_prompt.encode())
    for skill in draft.spec.skills:
        metadata = yaml.safe_dump({"name": skill.name, "description": skill.description},
                                  allow_unicode=True, sort_keys=True)
        source.add(f"skills/{skill.name}/SKILL.md",
                   ("---\n" + metadata + "---\n" + skill.instructions).encode())
        for file in skill.files:
            raw = (file.content.encode() if file.content is not None
                   else base64.b64decode(file.content_base64 or "", validate=True))
            source.add(f"skills/{skill.name}/{file.path}", raw)
    for tool in draft.spec.python_tools:
        source.add(f"tools/{tool.name}.py", tool.code.encode())
    digest = hashlib.sha256("\n".join(f"{f.path}:{f.digest}" for f in source.files).encode())
    return DeepagentsProjectSource(
        revision=draft.revision, filename=draft.spec.name, digest=digest.hexdigest(),
        framework_version="", files=tuple(source.files),
    )
