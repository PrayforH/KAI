"""Idempotent Skill storage migration; --inline restores old-reader compatibility.

Run only after both API and Worker support SkillBlobStore. Each row is locked,
verified by round-trip comparison, and committed separately; content identity,
revision numbers and timestamps never change. No Skill object is deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import cast

from sqlalchemy import Table, select

from harness.config import Settings
from harness.storage.database import create_database
from harness.storage.minio import MinioArtifactStore
from harness.storage.models import AgentDraftRevisionRow, AgentDraftRow, AgentVersionRow
from harness.storage.skill_blobs import SkillBlobStore


async def migrate(*, inline: bool = False) -> dict[str, int]:
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    blobs = SkillBlobStore(
        MinioArtifactStore(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            bucket=settings.minio_bucket,
            secure=settings.minio_secure,
        )
    )
    counts: dict[str, int] = {}
    try:
        for model in (AgentDraftRow, AgentDraftRevisionRow, AgentVersionRow):
            keys = list(cast(Table, model.__table__).primary_key.columns)
            async with sessions() as db:
                identities = (await db.execute(select(*keys))).all()
            changed = 0
            for identity in identities:
                async with sessions() as db:
                    row = await db.get(model, tuple(identity), with_for_update=True)
                    if row is None:
                        continue
                    original = await blobs.transform(row.tenant_id, row.payload, inline=True)
                    updated = await blobs.transform(row.tenant_id, original, inline=inline)
                    restored = await blobs.transform(row.tenant_id, updated, inline=True)
                    if restored != original:
                        raise ValueError("Skill migration round-trip mismatch")
                    if updated != row.payload:
                        row.payload = updated
                        await db.commit()
                        changed += 1
            counts[model.__tablename__] = changed
    finally:
        await engine.dispose()
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inline", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(migrate(inline=args.inline))))
