"""Register or move one owner computer (operator maintenance, no API).

``adopt`` records an administrator-provisioned computer that the registry does
not know yet. ``switch`` moves an existing owner row onto another provider
while keeping its identity and history. Both are idempotent; neither moves
files, and neither touches a computer that still has a live holder. Run inside
a production container against the production database.
"""

import argparse
import asyncio
import re

from harness.computer.models import AssistantComputer
from harness.computer.registry import ComputerRegistry
from harness.storage.database import create_database


def _validate(computer_id: str, image: str) -> None:
    if re.fullmatch(r"computer_[a-f0-9]{32}", computer_id) is None:
        raise SystemExit("computer id must be computer_<32 hex>")
    if re.fullmatch(r"sha256:[a-f0-9]{64}", image) is None:
        raise SystemExit("image must be an immutable sha256 digest")


async def run(database_url: str, mode: str, arguments: argparse.Namespace) -> str:
    _validate(arguments.computer_id, arguments.image)
    engine, sessions = create_database(database_url)
    try:
        registry = ComputerRegistry(sessions)
        row: AssistantComputer
        if mode == "adopt":
            row = await registry.adopt(
                arguments.tenant_id,
                arguments.user_id,
                provider=arguments.provider,
                image=arguments.image,
                computer_id=arguments.computer_id,
                volume_ref=arguments.volume_ref,
            )
        else:
            row = await registry.switch_provider(
                arguments.tenant_id,
                arguments.user_id,
                computer_id=arguments.computer_id,
                provider=arguments.provider,
                image=arguments.image,
                volume_ref=arguments.volume_ref,
            )
        return (
            f"{mode} {row.computer_id} provider={row.provider} image={row.image[:19]} "
            f"volume={row.volume_ref} state={row.state.value} "
            f"provider_ref={row.provider_ref} revision={row.revision}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("adopt", "switch"))
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--computer-id", required=True)
    parser.add_argument("--provider", default="vm-computer")
    parser.add_argument("--image", required=True)
    parser.add_argument("--volume-ref", required=True)
    arguments = parser.parse_args()
    print(asyncio.run(run(arguments.database_url, arguments.mode, arguments)))


if __name__ == "__main__":
    main()
