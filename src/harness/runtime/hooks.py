"""Safe SDK diagnostics that never forward environment values."""

from collections import deque
from collections.abc import Callable

SDK_DIAGNOSTIC_MAX_LINES = 12
SDK_DIAGNOSTIC_MAX_CHARS = 2_000


def redact_sdk_stderr(line: str) -> str:
    lowered = line.lower()
    if any(
        marker in lowered
        for marker in (
            "api_key",
            "api-key",
            "auth_token",
            "auth-token",
            "authorization",
            "credential",
            "secret",
            "token=",
            "key=",
        )
    ):
        return "[redacted sdk diagnostic]"
    return line.strip()


class SdkDiagnosticTail:
    """Bounded, redacted stderr callback retained only for failure diagnosis."""

    def __init__(self) -> None:
        self._lines: deque[str] = deque(maxlen=SDK_DIAGNOSTIC_MAX_LINES)

    def __call__(self, line: str) -> None:
        safe = redact_sdk_stderr(line)
        if safe:
            self._lines.append(safe[:SDK_DIAGNOSTIC_MAX_CHARS])

    def summary(self) -> str:
        rendered = " | ".join(self._lines)
        if not rendered:
            return "unavailable"
        return rendered[-SDK_DIAGNOSTIC_MAX_CHARS:]


def sdk_diagnostic_summary(callback: Callable[[str], None] | None) -> str:
    if isinstance(callback, SdkDiagnosticTail):
        return callback.summary()
    return "unavailable"
