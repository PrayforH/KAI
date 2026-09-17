"""The remote Claude CLI version contract shared by every sandbox backend."""

from pathlib import Path

from harness.sandbox.claude_cli import (
    BANNER_SUFFIX,
    banner_matches,
    bundled_cli_path,
    expected_banner,
    install_command,
    version_pin,
    version_text,
)


def test_an_empty_setting_follows_the_bundled_cli() -> None:
    assert version_pin("") is None
    assert version_pin("   ") is None
    assert version_pin(" 2.1.259 ") == "2.1.259"
    assert expected_banner(None) is None
    assert expected_banner("2.1.259") == f"2.1.259 {BANNER_SUFFIX}"


def test_an_unpinned_cli_is_accepted_at_whatever_version_it_reports() -> None:
    assert banner_matches("2.1.259 (Claude Code)", None)
    assert banner_matches("2.1.274 (Claude Code)", None)


def test_a_missing_cli_never_passes_as_a_version_match() -> None:
    assert not banner_matches("", None)
    assert not banner_matches("   ", None)
    assert not banner_matches("", "2.1.259")


def test_a_pin_requires_the_exact_banner() -> None:
    assert banner_matches("2.1.259 (Claude Code)", "2.1.259")
    assert not banner_matches("2.1.206 (Claude Code)", "2.1.259")
    assert not banner_matches("2.1.259", "2.1.259")


def test_version_text_falls_back_to_stderr() -> None:
    assert version_text("2.1.259 (Claude Code)\n", "") == "2.1.259 (Claude Code)"
    assert version_text("", "2.1.259 (Claude Code)\n") == "2.1.259 (Claude Code)"
    assert version_text("", "") == ""


def test_the_installer_is_only_pinned_when_a_version_is_configured() -> None:
    unpinned = install_command(None)
    assert unpinned.endswith("install.sh | bash")
    assert "-s " not in unpinned
    pinned = install_command("2.1.206; rm -rf /")
    assert "bash -s '2.1.206; rm -rf /'" in pinned


def test_the_bundled_cli_is_the_sdk_binary() -> None:
    path = bundled_cli_path()
    assert path.name == "claude"
    assert path.parent.name == "_bundled"
    assert isinstance(path, Path)
