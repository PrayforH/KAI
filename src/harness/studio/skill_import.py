"""Safe import of an editable Agent Skill from Markdown or a ZIP archive."""

from __future__ import annotations

import base64
import hashlib
import io
import re
import stat
import zipfile
from pathlib import PurePosixPath
from typing import cast

import yaml

from harness.studio.models import DraftSkill, DraftSkillFile, ImportedSkill

MAX_SKILL_UPLOAD_BYTES = 100 * 1024 * 1024
_MAX_SKILL_UNPACKED_BYTES = 256 * 1024 * 1024
_MAX_SKILL_FILES = 20_000
_SKILL_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_SECRET_PATH = re.compile(
    r"(^|/)(?:\.env(?:\..+)?|credentials?(?:\..+)?|secrets?(?:\..+)?|"
    r"id_rsa|id_ed25519|[^/]+\.(?:pem|key|p12|pfx))$",
    re.IGNORECASE,
)
_SECRET_CONTENT = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-(?:lf-|live-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{20,}\b"),
)
_EXECUTABLE_SUFFIXES = {".py", ".sh", ".bash", ".js", ".ts", ".mjs"}
_DEPENDENCY_FILES = {
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "uv.lock",
}


class SkillImportError(ValueError):
    """The uploaded Skill cannot be safely represented by the Studio schema."""


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not normalized or not normalized[0].isalpha():
        normalized = f"imported-skill-{hashlib.sha256(value.encode()).hexdigest()[:8]}"
    return normalized[:64].rstrip("-")


def _parse_skill_markdown(
    source: str,
    *,
    fallback_name: str,
) -> tuple[str, str, str, tuple[str, ...]]:
    lines = source.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillImportError("SKILL.md 缺少 YAML frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as error:
        raise SkillImportError("SKILL.md frontmatter 未闭合") from error
    try:
        raw_metadata = cast(object, yaml.safe_load("\n".join(lines[1:end])))
    except yaml.YAMLError as error:
        raise SkillImportError("SKILL.md frontmatter 不是有效 YAML") from error
    if not isinstance(raw_metadata, dict):
        raise SkillImportError("SKILL.md frontmatter 必须是对象")
    metadata = {str(key): value for key, value in cast(dict[object, object], raw_metadata).items()}
    raw_name = str(metadata.get("name") or fallback_name).strip()
    name = raw_name if _SKILL_NAME.fullmatch(raw_name) else _safe_name(raw_name)
    warnings: list[str] = []
    if name != raw_name:
        warnings.append(f"Skill 名称已规范化为 {name}")
    description = str(metadata.get("description") or f"从上传文件安装的 {name} Skill").strip()
    instructions = "\n".join(lines[end + 1 :]).strip()
    if not instructions:
        raise SkillImportError("SKILL.md 工作流说明为空")
    return name, description[:500], instructions + "\n", tuple(warnings)


def _decode_text(content: bytes, *, path: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkillImportError(f"当前仅支持 UTF-8 Skill 文件：{path}") from error


def _reject_secret(path: str, content: bytes) -> None:
    if _SECRET_PATH.search(path):
        raise SkillImportError(f"Skill 包含凭据类文件，已拒绝安装：{path}")
    text = content.decode("utf-8", errors="ignore")
    if any(pattern.search(text) for pattern in _SECRET_CONTENT):
        raise SkillImportError(f"Skill 文件疑似包含密钥，已拒绝安装：{path}")


def _archive_files(content: bytes) -> tuple[str, list[tuple[str, bytes]]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError) as error:
        raise SkillImportError("上传文件不是有效 ZIP") from error
    with archive:
        members: list[tuple[str, bytes]] = []
        total = 0
        skill_paths: list[str] = []
        for item in archive.infolist():
            if item.is_dir() or item.filename.startswith("__MACOSX/"):
                continue
            path = PurePosixPath(item.filename)
            if path.name in {".DS_Store", ".env.example"} or any(
                part in {"__pycache__", ".git"} for part in path.parts
            ):
                continue
            mode = item.external_attr >> 16
            if (
                path.is_absolute()
                or "\\" in item.filename
                or any(part in {"", ".", ".."} for part in path.parts)
                or stat.S_ISLNK(mode)
            ):
                raise SkillImportError(f"Skill ZIP 包含不安全路径：{item.filename}")
            total += item.file_size
            if total > _MAX_SKILL_UNPACKED_BYTES:
                raise SkillImportError("Skill ZIP 解压后超过 256 MiB")
            if len(members) >= _MAX_SKILL_FILES:
                raise SkillImportError("Skill ZIP 文件数量超过 20000")
            payload = archive.read(item)
            _reject_secret(path.as_posix(), payload)
            members.append((path.as_posix(), payload))
            if path.name == "SKILL.md":
                skill_paths.append(path.as_posix())
        if len(skill_paths) != 1:
            raise SkillImportError("Skill ZIP 必须且只能包含一个 SKILL.md")
        return skill_paths[0], members


def import_skill(content: bytes, *, filename: str) -> ImportedSkill:
    if not content:
        raise SkillImportError("Skill 上传内容为空")
    if len(content) > MAX_SKILL_UPLOAD_BYTES:
        raise SkillImportError("Skill 上传文件超过 100 MiB")

    warnings: list[str] = []
    findings: list[str] = []
    if content.startswith(b"PK\x03\x04"):
        skill_path, members = _archive_files(content)
        root = str(PurePosixPath(skill_path).parent)
        root_prefix = "" if root == "." else f"{root}/"
        source = _decode_text(
            next(payload for path, payload in members if path == skill_path),
            path=skill_path,
        )
        fallback_name = PurePosixPath(root).name if root != "." else PurePosixPath(filename).stem
        files: list[DraftSkillFile] = []
        binary_files = 0
        for path, payload in members:
            if path == skill_path or not path.startswith(root_prefix):
                continue
            relative = path.removeprefix(root_prefix)
            if relative.startswith(".") or "/." in relative:
                continue
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                files.append(
                    DraftSkillFile(
                        path=relative,
                        contentBase64=base64.b64encode(payload).decode("ascii"),
                    )
                )
                binary_files += 1
            else:
                files.append(DraftSkillFile(path=relative, content=text))
            relative_path = PurePosixPath(relative)
            if relative_path.suffix.lower() in _EXECUTABLE_SUFFIXES:
                findings.append(f"包含可执行脚本：{relative}")
            if relative_path.name.lower() in _DEPENDENCY_FILES:
                findings.append(f"包含依赖声明：{relative}")
        if binary_files:
            warnings.append(f"已保留 {binary_files} 个二进制 asset")
    else:
        source = _decode_text(content, path=filename or "SKILL.md")
        fallback_name = PurePosixPath(filename or "skill").stem
        files = []

    name, description, instructions, name_warnings = _parse_skill_markdown(
        source,
        fallback_name=fallback_name,
    )
    warnings.extend(name_warnings)
    if findings:
        warnings.append("脚本和依赖已作为快照内容导入；实际执行或安装依赖仍由运行时权限门判定")
    digest = hashlib.sha256(content).hexdigest()
    return ImportedSkill(
        skill=DraftSkill(
            name=name,
            description=description,
            instructions=instructions,
            files=tuple(files),
        ),
        sourceContentHash=digest,
        riskLevel="review" if findings else "low",
        findings=tuple(dict.fromkeys(findings)),
        warnings=tuple(warnings),
    )
