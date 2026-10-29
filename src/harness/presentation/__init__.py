"""Intelligent UI: a validated, durable presentation protocol for agent answers."""

from harness.presentation.catalog import (
    UI_BLOCK_CONTRACT,
    UICapabilityCatalog,
    default_catalog,
)
from harness.presentation.projection import presentations_for_events
from harness.presentation.schema import (
    PRESENTATION_POLICY_VERSION,
    UI_SCHEMA_VERSION,
    Presentation,
    PresentationBlock,
    validate_block,
)

__all__ = [
    "PRESENTATION_POLICY_VERSION",
    "UI_BLOCK_CONTRACT",
    "UI_SCHEMA_VERSION",
    "Presentation",
    "PresentationBlock",
    "UICapabilityCatalog",
    "default_catalog",
    "presentations_for_events",
    "validate_block",
]
