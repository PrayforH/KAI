"""Adopt an existing, administrator-provisioned computer into the registry.

Maintenance entry for the 79 migration window: the operator verifies the
runner container, root disk and work volume out of band, then records the
canonical identity so Worker routing switches in one revision. Run inside the
API container against the production database. Idempotent.
"""

import argparse
import asyncio
import re

from sqlalchemy import select

from harness.computer.registry import ComputerRegistry
from harness.storage.database import create_database


async def adopt(
    database_url: str,
    *,
    tenant_id: str,
    user_id: str,
    provider: str,
    image: str,
    computer_id: str,
    volume_ref: str,
) -> str:
    if re.fullmatch(r"computer_[a-f0-9]{32}", computer_id) is None:
        raise SystemExit("computer id must be computer_<32 hex>")
    if re.fullmatch(r"sha256:[a-f0-9]{64}", image) is None:
        raise SystemExit("image must be the guest base sha256 digest")
    engine, sessions = create_database(database_url)
    try:
        registry = ComputerRegistry(sessions)
        row = await registry.adopt(
            tenant_id,
            user_id,
            provider=provider,
            image=image,
            computer_id=computer_id,
            volume_ref=volume_ref,
        )
        # Idempotency check by listing what the registry now owns.
        async with sessions() as session:
            found = await session.scalar(
                select(row.__class__).where(
                    row.__class__.tenant_id == tenant_id, row.__class__.user_id == user_id
                )
            )
            assert found is not None
        return f"adopted {row.computer_id} provider={row.provider} volume={row.volume_ref}"
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--provider", default="vm-computer")
    parser.add_argument("--image", required=True)
    parser.add_argument("--computer-id", required=True)
    parser.add_argument("--volume-ref", required=True)
    arguments = parser.parse_args()
    print(
        asyncio.run(
            adopt(
                arguments.database_url,
                tenant_id=arguments.tenant_id,
                user_id=arguments.user_id,
                provider=arguments.provider,
                image=arguments.image,
                computer_id=arguments.computer_id,
                volume_ref=arguments.volume_ref,
            )
        )
    )


if __name__ == "__main__":
    main()
