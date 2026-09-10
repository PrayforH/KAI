"""Load reviewed third-party Agent Skills vendored under ``platform-skills/``.

Every vendored Skill keeps its upstream files byte-for-byte (including the
upstream LICENSE) so the platform catalog can attribute and audit provenance.
Only the first-party packages in :mod:`harness.studio.platform_skills` carry
platform-authored text; this module never rewrites upstream content beyond
trimming the catalog-facing description to the model's field budget.
"""

from __future__ import annotations

import base64
import re
from functools import lru_cache
from pathlib import Path

import yaml

from harness.studio.models import DraftSkill, DraftSkillFile

_MAX_DESCRIPTION_CHARS = 500
_TEXT_SUFFIXES = frozenset(
    {
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".sh",
        ".css",
        ".html",
        ".xml",
        ".xsd",
        ".csv",
        ".toml",
        ".cfg",
        ".ini",
        ".gitignore",
    }
)

# Vendored Skill directory -> (upstream repository URL, pinned revision, license).
# Licenses were verified per directory against the upstream LICENSE file:
# MiniMax packages are MIT; the Anthropic packages listed here are Apache-2.0.
VENDORED_SOURCES: dict[str, tuple[str, str, str]] = {
    "minimax-docx": (
        "https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/minimax-docx",
        "60aaae52bb2af8162732751a4332f62a5fef518b",
        "MIT",
    ),
    "minimax-xlsx": (
        "https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/minimax-xlsx",
        "60aaae52bb2af8162732751a4332f62a5fef518b",
        "MIT",
    ),
    "minimax-pdf": (
        "https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/minimax-pdf",
        "60aaae52bb2af8162732751a4332f62a5fef518b",
        "MIT",
    ),
    "pptx-generator": (
        "https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/skills/pptx-generator",
        "60aaae52bb2af8162732751a4332f62a5fef518b",
        "MIT",
    ),
    "color-font-skill": (
        "https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/plugins/pptx-plugin/skills/color-font-skill",
        "60aaae52bb2af8162732751a4332f62a5fef518b",
        "MIT",
    ),
    "design-style-skill": (
        "https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/plugins/pptx-plugin/skills/design-style-skill",
        "60aaae52bb2af8162732751a4332f62a5fef518b",
        "MIT",
    ),
    "ppt-editing-skill": (
        "https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/plugins/pptx-plugin/skills/ppt-editing-skill",
        "60aaae52bb2af8162732751a4332f62a5fef518b",
        "MIT",
    ),
    "slide-making-skill": (
        "https://github.com/MiniMax-AI/skills/blob/60aaae52bb2af8162732751a4332f62a5fef518b/plugins/pptx-plugin/skills/slide-making-skill",
        "60aaae52bb2af8162732751a4332f62a5fef518b",
        "MIT",
    ),
    "skill-creator": (
        "https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/skill-creator",
        "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f",
        "Apache-2.0",
    ),
    "mcp-builder": (
        "https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/mcp-builder",
        "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f",
        "Apache-2.0",
    ),
    "internal-comms": (
        "https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/internal-comms",
        "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f",
        "Apache-2.0",
    ),
    "theme-factory": (
        "https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/theme-factory",
        "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f",
        "Apache-2.0",
    ),
    "frontend-design": (
        "https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/frontend-design",
        "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f",
        "Apache-2.0",
    ),
    "web-artifacts-builder": (
        "https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/web-artifacts-builder",
        "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f",
        "Apache-2.0",
    ),
    "webapp-testing": (
        "https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/webapp-testing",
        "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f",
        "Apache-2.0",
    ),
    "canvas-design": (
        "https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/canvas-design",
        "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f",
        "Apache-2.0",
    ),
    "algorithmic-art": (
        "https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/algorithmic-art",
        "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f",
        "Apache-2.0",
    ),
}

_SKILL_MARKDOWN = "SKILL.md"
_EXCLUDED_NAMES = frozenset({".DS_Store", ".gitignore"})


def vendored_skills_root() -> Path | None:
    """Resolve the vendored Skill tree across image and checkout layouts."""

    import os

    configured = os.environ.get("HARNESS_PLATFORM_SKILLS_ROOT")
    candidates = [
        Path(configured) if configured else None,
        Path("/app/platform-skills"),
        Path(__file__).resolve().parents[3] / "platform-skills",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_dir():
            return candidate
    return None


def _parse_frontmatter(path: Path) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"vendored Skill is missing frontmatter: {path}")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError(f"vendored Skill frontmatter is not closed: {path}")
    metadata = yaml.safe_load(text[4:end]) or {}
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"vendored Skill has no name: {path}")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"vendored Skill has no description: {path}")
    body = text[end + 4 :].lstrip("\n")
    return name.strip(), description, body


def _catalog_description(description: str) -> str:
    """Collapse upstream whitespace and fit the model's description budget.

    Upstream descriptions are authored for the Claude CLI, which accepts folded
    block scalars and longer budgets; the platform model caps at 500 characters.
    """

    collapsed = re.sub(r"\s+", " ", description).strip()
    if len(collapsed) <= _MAX_DESCRIPTION_CHARS:
        return collapsed
    clipped = collapsed[: _MAX_DESCRIPTION_CHARS - 1]
    if " " in clipped:
        clipped = clipped[: clipped.rfind(" ")]
    return clipped.rstrip(" ,;:") + "…"


def _file_entries(skill_root: Path) -> tuple[DraftSkillFile, ...]:
    entries: list[DraftSkillFile] = []
    for path in sorted(skill_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(skill_root).as_posix()
        if relative == _SKILL_MARKDOWN or path.name in _EXCLUDED_NAMES:
            continue
        raw = path.read_bytes()
        if path.suffix.lower() in _TEXT_SUFFIXES:
            try:
                entries.append(DraftSkillFile(path=relative, content=raw.decode("utf-8")))
                continue
            except UnicodeDecodeError:
                pass
        entries.append(
            DraftSkillFile(
                path=relative,
                contentBase64=base64.b64encode(raw).decode("ascii"),
                sizeBytes=len(raw),
            )
        )
    return tuple(entries)


@lru_cache(maxsize=1)
def _load_vendored_skills_cached() -> tuple[DraftSkill, ...]:
    return _load_vendored_skills_uncached()


def _load_vendored_skills_uncached() -> tuple[DraftSkill, ...]:
    """Return every vendored Skill as a DraftSkill snapshot, or nothing."""

    root = vendored_skills_root()
    if root is None:
        return ()
    skills: list[DraftSkill] = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or directory.name not in VENDORED_SOURCES:
            continue
        manifest = directory / _SKILL_MARKDOWN
        if not manifest.is_file():
            continue
        name, description, body = _parse_frontmatter(manifest)
        if name != directory.name:
            raise ValueError(
                f"vendored Skill directory must match frontmatter name: "
                f"{directory.name} != {name}"
            )
        skills.append(
            DraftSkill(
                name=name,
                description=_catalog_description(description),
                instructions=body,
                files=_file_entries(directory),
            )
        )
    return tuple(skills)


def load_vendored_skills() -> tuple[DraftSkill, ...]:
    """Return vendored Skills, parsing upstream files at most once per process.

    The tree carries multi-megabyte assets; re-reading and re-hashing it on
    every catalog read would dominate request latency.
    """

    return _load_vendored_skills_cached()
