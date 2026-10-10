"""The presentation capability negotiation endpoint.

A client asks what this deployment can render before it sends anything, so an
older console or a second host can degrade to ``fallback_text`` deliberately
instead of discovering an unknown renderer mid-answer. The answer is computed
from the server catalog; a client can never widen it by declaring support.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from harness.api.dependencies import ApiContainer, Identity, get_container, require_identity
from harness.presentation import default_catalog

router = APIRouter(tags=["intelligent-ui"])


@router.get("/ui/capabilities")
async def get_ui_capabilities(
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> dict[str, Any]:
    """Describe the schema, renderers and limits the current client must honour."""

    del identity, container
    return default_catalog().describe()
