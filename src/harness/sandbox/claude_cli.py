"""One source of truth for the remote Claude CLI version.

The CLI that runs inside a sandbox is the binary bundled with the pinned
``claude-agent-sdk``. Carrying a second, hand-written version literal in
configuration invites drift: the literal stays behind the SDK, and provisioning
then compares an installed CLI against a version no artifact contains any more —
which fails closed on a perfectly healthy deployment.

Configuration therefore only *overrides* the version. Left empty, provisioning
accepts whatever the bundle reports, so the SDK pin in the lockfile is the
single source of truth for every backend.
"""

from __future__ import annotations

import shlex
from pathlib import Path

BANNER_SUFFIX = "(Claude Code)"
INSTALLER_URL = "https://claude.ai/install.sh"


def bundled_cli_path() -> Path:
    """The CLI shipped inside the installed ``claude-agent-sdk``."""

    import claude_agent_sdk

    return Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"


def version_pin(version: str) -> str | None:
    """Normalize a configured CLI version; ``None`` means "follow the bundle"."""

    return version.strip() or None


def expected_banner(pin: str | None) -> str | None:
    """The ``--version`` output a pinned CLI must print."""

    return f"{pin} {BANNER_SUFFIX}" if pin else None


def version_text(stdout: str, stderr: str) -> str:
    """A CLI version banner, which some builds print on stderr."""

    return stdout.strip() or stderr.strip()


def banner_matches(observed: str, pin: str | None) -> bool:
    """Whether an observed banner satisfies the pin.

    Without a pin any non-empty banner counts: the SDK-locked bundle decides.
    An empty banner never matches, so a missing CLI cannot pass as a version.
    """

    seen = observed.strip()
    if not seen:
        return False
    expected = expected_banner(pin)
    return True if expected is None else seen == expected


def install_command(pin: str | None) -> str:
    """The installer invocation, pinned only when a version was configured."""

    script = (
        "set -o pipefail; "
        "curl -fsSL --retry 5 --retry-delay 2 --retry-all-errors "
        f"{INSTALLER_URL}"
    )
    if pin is None:
        return f"{script} | bash"
    return f"{script} | bash -s {shlex.quote(pin)}"
