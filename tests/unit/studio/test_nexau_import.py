import struct
import zlib
from io import BytesIO
from zipfile import ZipFile

import pytest

from harness.studio.bundle_import import AgentBundleImportError, parse_agent_bundle


def rar_store(files: dict[str, bytes]) -> bytes:
    """Small RAR 4 stored archive, including real header and data CRCs."""

    def header(kind: int, flags: int, payload: bytes) -> bytes:
        body = struct.pack("<BHH", kind, flags, len(payload) + 7) + payload
        return struct.pack("<H", zlib.crc32(body) & 0xFFFF) + body

    data = b"Rar!\x1a\x07\x00" + header(0x73, 0, b"\x00" * 6)
    for name, content in files.items():
        filename = name.encode()
        metadata = struct.pack(
            "<IIBIIBBHI",
            len(content),
            len(content),
            2,
            zlib.crc32(content),
            0,
            20,
            0x30,
            len(filename),
            32,
        )
        data += header(0x74, 0x8000, metadata + filename) + content
    return data + header(0x7B, 0, b"")


def archive_bytes(files: dict[str, bytes], kind: str) -> bytes:
    if kind == "rar":
        return rar_store(files)
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


@pytest.mark.parametrize("kind", ["zip", "rar"])
def test_import_wrapped_named_config_without_skills(kind: str) -> None:
    files = {
        "package/code_agent.yaml": (b"name: demo\nsystem_prompt: Answer briefly.\n"
                                    b"system_prompt_type: string\n")
    }
    result = parse_agent_bundle(archive_bytes(files, kind))
    assert "Answer briefly." in result.spec.system_prompt
    assert result.spec.name == "demo"
    assert result.spec.skills == ()


@pytest.mark.parametrize("kind", ["zip", "rar"])
def test_root_selection_excludes_referenced_subagents(kind: str) -> None:
    files = {
        "code_agent.yaml": (b"type: agent\nname: primary\nsystem_prompt: system.md\n"
                            b"sub_agents:\n  - config_path: sub_agent.yaml\n"),
        "system.md": b"Build a report.",
        "sub_agent.yaml": b"type: agent\nname: child\nsystem_prompt: system.md\n",
    }
    result = parse_agent_bundle(archive_bytes(files, kind))
    assert result.spec.name == "primary"
    assert any("子智能体" in warning for warning in result.warnings)


@pytest.mark.parametrize("kind", ["zip", "rar"])
@pytest.mark.parametrize("name", ["../agent.yaml", "/agent.yaml", "C:/agent.yaml"])
def test_archive_paths_are_rejected_without_extracting(kind: str, name: str) -> None:
    with pytest.raises(AgentBundleImportError, match="不安全"):
        parse_agent_bundle(archive_bytes({name: b"type: agent"}, kind))


def test_ambiguous_configs_are_explained() -> None:
    with pytest.raises(AgentBundleImportError, match="多个根"):
        parse_agent_bundle(
            archive_bytes(
                {name: b"type: agent\nname: demo" for name in ["a.yaml", "b.yaml"]}, "zip"
            )
        )


def test_broken_rar_is_explained() -> None:
    with pytest.raises(AgentBundleImportError, match="RAR"):
        parse_agent_bundle(b"Rar!\x1a\x07\x00broken")
