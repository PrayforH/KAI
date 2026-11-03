"""Public, opaque identity for a registry's current physical target."""

import hashlib
import json


def desktop_binding(provider: str, image: str, volume_ref: str) -> str:
    return hashlib.sha256(json.dumps([provider, image, volume_ref]).encode()).hexdigest()
