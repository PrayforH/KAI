"""Text that PostgreSQL can store.

PostgreSQL text and ``jsonb`` reject U+0000. Model runtimes, shells and
third-party tools can still emit it from arbitrary output, so it is removed
once at the persistence boundary instead of at every producer.
"""

from collections.abc import Mapping, Sequence
from typing import Any


def strip_nul(value: str) -> str:
    """Return ``value`` without U+0000 characters."""

    return value.replace("\x00", "") if "\x00" in value else value


def json_safe(value: Any) -> Any:
    """Recursively remove U+0000 from a JSON-compatible value, keys included."""

    if isinstance(value, str):
        return strip_nul(value)
    if isinstance(value, Mapping):
        return {
            strip_nul(key) if isinstance(key, str) else key: json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [json_safe(item) for item in value]
    return value
