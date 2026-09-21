"""A whole catalog read must survive a deployment without the vendored tree.

``platform-skills/`` is optional content: the vendored ``skill-creator`` package is
one entry among several, and a deployment that ships without it (or whose frontmatter
fails to parse) must still serve capability catalogs instead of failing every read.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from harness.studio.catalog import default_capability_catalog
from harness.studio.catalog_repository import InMemoryCapabilityCatalogRepository
from harness.studio.catalog_service import CapabilityCatalogService
from harness.studio.repositories import InMemoryAgentDraftRepository

NOW = datetime(2026, 7, 17, tzinfo=UTC)


@pytest.mark.asyncio
async def test_catalog_read_survives_a_missing_vendored_creator(monkeypatch) -> None:
    vendored = default_capability_catalog()
    without_creator = vendored.model_copy(
        update={
            "skills": tuple(
                skill for skill in vendored.skills if skill.package_id != "skill-creator"
            )
        }
    )
    monkeypatch.setattr(
        "harness.studio.catalog_service.default_capability_catalog",
        lambda: without_creator,
    )
    service = CapabilityCatalogService(
        InMemoryCapabilityCatalogRepository(),
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )

    record = await service.get("tenant-a")

    assert "skill-creator" not in {skill.package_id for skill in record.catalog.skills}
    assert record.catalog.skills, "the rest of the catalog still resolves"
