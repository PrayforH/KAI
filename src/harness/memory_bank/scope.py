"""Agent identity uses the registry's immutable owner/name pair, never its label."""

import hashlib


def policy_key(user_id: str, agent_name: str, owner_id: str | None = None) -> str:
    if owner_id is None or owner_id == user_id:
        return agent_name
    return "scope_" + hashlib.sha256(f"{owner_id}\0{agent_name}".encode()).hexdigest()
