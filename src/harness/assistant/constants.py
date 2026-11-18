"""Stable identity of the private, cross-channel assistant conversation."""

PERSONAL_ASSISTANT_THREAD = "kai-personal-assistant-v1"
# The coordinator's seat inside a group; the only role that may manage the roster.
GROUP_PRIMARY_MEMBER_ID = "primary"
GROUP_THREAD_PREFIX = "assistant_group_"


def group_thread_id(group_id: str, member_id: str) -> str:
    """Each seat has an isolated thread, shared by dispatch and recovery."""
    return f"{GROUP_THREAD_PREFIX}{group_id}_{member_id}"


def primary_specialist_id(tenant_id: str, user_id: str) -> str:
    """Presentation identity for the existing conversation; never an authority grant."""
    from hashlib import sha256

    digest = sha256(f"{tenant_id}\0{user_id}".encode()).hexdigest()[:24]
    return f"specialist_primary_{digest}"
