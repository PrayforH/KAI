"""Versioned Studio-only metadata embedded in reproducible Agent bundles."""

from typing import Literal

from pydantic import Field

from harness.studio.models import DraftTaskContract, StudioModel

STUDIO_BUNDLE_METADATA_FILENAME = "studio.json"


class StudioBundleMetadata(StudioModel):
    """Authoring fields that cannot be recovered from ``agent.yaml`` alone."""

    api_version: Literal["harness.studio/v1"] = Field(alias="apiVersion")
    kind: Literal["AgentDraftMetadata"]
    description: str = Field(min_length=1, max_length=500)
    task_contract: DraftTaskContract | None = Field(default=None, alias="taskContract")
    execution_profile: str = Field(
        alias="executionProfile",
        min_length=1,
    )
