"""Admission checks for declared specialist capabilities, independent of UI state."""

from collections.abc import Mapping

from harness.core.errors import ConflictError

LOCAL_TOOL_KEYS = frozenset(
    {"run_commands", "access_private_files", "use_connected_apps", "run_background_tasks"}
)


def pending_local_tools(config: Mapping[str, object]) -> list[str]:
    """Legacy bots without declarations remain compatible; malformed policy fails closed."""
    lists: dict[str, list[str]] = {}
    for name in ("required_local_tools", "local_tool_permissions"):
        value = config.get(name, [])
        if not isinstance(value, list) or any(
            not isinstance(key, str) or key not in LOCAL_TOOL_KEYS for key in value
        ):
            raise ConflictError(f"{name} 包含不支持的本地能力")
        lists[name] = value
    granted = set(lists["local_tool_permissions"])
    return list(dict.fromkeys(key for key in lists["required_local_tools"] if key not in granted))


def require_local_tools(config: Mapping[str, object]) -> None:
    pending = pending_local_tools(config)
    if pending:
        raise ConflictError("待授权本地能力：" + "、".join(pending))
