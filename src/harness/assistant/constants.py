"""Stable identity of the private, cross-channel assistant conversation."""

PERSONAL_ASSISTANT_THREAD = "kai-personal-assistant-v1"


def primary_specialist_id(tenant_id: str, user_id: str) -> str:
    """Presentation identity for the existing conversation; never an authority grant."""
    from hashlib import sha256

    digest = sha256(f"{tenant_id}\0{user_id}".encode()).hexdigest()[:24]
    return f"specialist_primary_{digest}"
