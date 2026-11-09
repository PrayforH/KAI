"""Public, opaque identity for a registry's current physical target.

The binding names *which* computer a viewer is looking at, not which image
version it runs. Keeping the image out of the hash means a routine image
upgrade no longer invalidates a page that was already open, while a different
provider or work disk is still rejected.
"""

import hashlib
import json


def desktop_binding(provider: str, image: str, volume_ref: str) -> str:
    return hashlib.sha256(json.dumps([provider, volume_ref]).encode()).hexdigest()
