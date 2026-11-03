"""Owner-visible computer selection; private endpoints stay on the control node.

A target is provisioned by the operator. Users select only an allowed opaque ID.
The existing registry performs the atomic idle-only switch and retains history.
"""

from pydantic import BaseModel, ConfigDict, Field

from harness.computer.binding import desktop_binding
from harness.computer.registry import ComputerRegistry
from harness.core.errors import NotFoundError


class ComputerTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    target_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    label: str = Field(min_length=1, max_length=80)
    tenant_id: str
    user_id: str
    computer_id: str
    provider: str = Field(pattern=r"^(vm|remote)-computer$")
    image: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    volume_ref: str


class ComputerTargetService:
    def __init__(self, registry: ComputerRegistry, targets: list[ComputerTarget]):
        keys = [(t.tenant_id, t.user_id, t.target_id) for t in targets]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate owner computer target")
        self.registry, self.targets = registry, tuple(targets)

    async def view(self, tenant: str, user: str) -> dict[str, object]:
        current = await self.registry.get(tenant, user)
        allowed = [
            t
            for t in self.targets
            if (t.tenant_id, t.user_id, t.computer_id) == (tenant, user, current.computer_id)
        ]
        active = next(
            (
                t.target_id
                for t in allowed
                if (t.provider, t.image, t.volume_ref)
                == (current.provider, current.image, current.volume_ref)
            ),
            None,
        )
        return {
            "active_target": active,
            "revision": current.revision,
            "targets": [
                {
                    "target_id": t.target_id,
                    "label": t.label,
                    "binding_key": desktop_binding(t.provider, t.image, t.volume_ref),
                }
                for t in allowed
            ],
        }

    async def select(self, tenant: str, user: str, target_id: str) -> dict[str, object]:
        target = next(
            (
                t
                for t in self.targets
                if (t.tenant_id, t.user_id, t.target_id) == (tenant, user, target_id)
            ),
            None,
        )
        if target is None:
            raise NotFoundError("computer target is not available to this owner")
        await self.registry.switch_provider(
            tenant,
            user,
            computer_id=target.computer_id,
            provider=target.provider,
            image=target.image,
            volume_ref=target.volume_ref,
        )
        return await self.view(tenant, user)
