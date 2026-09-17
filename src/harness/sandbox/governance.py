"""Instance governance: what the platform runs versus what the platform thinks it owns.

Leases answer "who owns this sandbox"; governance answers "does that still match
reality". The two failure modes it exists for are opposite:

* **untracked** — a sandbox the platform is still paying for but no live lease
  claims: the Worker that created it died before it could release, or a probe
  left it behind. Only these are destroyed, and only after their lease expired.
* **missing** — a live lease whose sandbox is gone: the lease is stale and must
  not keep blocking the Session from acquiring a new one.

A provider that cannot enumerate instances (or destroy by id) reports
``platform_instances=None`` and is left alone, so the report never invents a
conclusion it could not verify.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from harness.sandbox.base import SandboxProvider
from harness.sandbox.lease import SandboxLeaseService

logger = logging.getLogger(__name__)


class SandboxInstance(BaseModel):
    """One sandbox the platform reports, with whatever metadata it kept."""

    sandbox_id: str
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime | None = None


class SandboxGovernanceReport(BaseModel):
    provider: str
    platform_version: str | None = None
    live_leases: int = 0
    platform_instances: int | None = None
    untracked: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    reclaimed: tuple[str, ...] = ()
    observed_at: datetime | None = None


class SandboxGovernanceService:
    def __init__(
        self,
        leases: SandboxLeaseService,
        provider: SandboxProvider,
        *,
        clock: Any | None = None,
        metrics: Any | None = None,
    ) -> None:
        self._leases = leases
        self._provider = provider
        self._clock = clock
        self._metrics = metrics
        self._lease_ttl = leases.default_ttl_seconds

    @property
    def can_reconcile(self) -> bool:
        return callable(getattr(self._provider, "inventory", None))

    async def report(self, *, tenant_id: str | None = None) -> SandboxGovernanceReport:
        """Inventory without changing anything."""

        return await self._reconcile(tenant_id=tenant_id, reclaim=False)

    async def reclaim_orphans(self) -> SandboxGovernanceReport:
        """Destroy untracked sandboxes and clear stale leases."""

        return await self._reconcile(tenant_id=None, reclaim=True)

    async def _destroy(self, sandbox_id: str) -> bool:
        """Destroy one instance, treating a platform failure as a skip."""

        reclaim_one = getattr(self._provider, "reclaim", None)
        if not callable(reclaim_one):
            return False
        try:
            return bool(await reclaim_one(sandbox_id))
        except Exception:  # noqa: BLE001 - one bad instance must not stop the sweep
            logger.exception(
                "sandbox reclamation failed", extra={"sandbox_id": sandbox_id}
            )
            return False

    async def _reconcile(
        self, *, tenant_id: str | None, reclaim: bool
    ) -> SandboxGovernanceReport:
        provider_name = str(getattr(self._provider, "provider_name", "unknown"))
        now = self._clock() if callable(self._clock) else datetime.now(UTC)
        owned = await self._leases.live(tenant_id)
        # An expired lease still holds its row (state stays active until a reaper
        # closes it) but no longer counts as ownership: that is exactly what lets
        # an abandoned sandbox be recognized as untracked.
        live = [lease for lease in owned if lease.is_live(now)]
        report = SandboxGovernanceReport(
            provider=provider_name,
            live_leases=len(live),
            observed_at=now,
        )
        inventory = getattr(self._provider, "inventory", None)
        if not callable(inventory):
            return report

        instances: Sequence[SandboxInstance] = await inventory()
        if tenant_id is not None:
            # A tenant view must not blame another tenant's sandboxes for being
            # untracked here; instances the platform left unlabeled stay visible
            # only in the unscoped operator report.
            instances = [
                instance
                for instance in instances
                if instance.metadata.get("harness.tenant") == tenant_id
            ]
        report = report.model_copy(update={"platform_instances": len(instances)})
        version_probe = getattr(self._provider, "platform_version", None)
        if callable(version_probe):
            report = report.model_copy(update={"platform_version": await version_probe()})

        tracked = {lease.sandbox_id for lease in live}
        present = {instance.sandbox_id for instance in instances}
        untracked = tuple(sorted(present - tracked))
        missing = tuple(sorted({lease.sandbox_id for lease in owned} - present))

        reclaimed: list[str] = []
        if reclaim:
            reclaim_one = getattr(self._provider, "reclaim", None)
            expired = {
                lease.sandbox_id: lease for lease in await self._leases.expired()
            }
            by_id = {instance.sandbox_id: instance for instance in instances}
            grace = timedelta(seconds=self._lease_ttl)
            for sandbox_id in untracked:
                lease = expired.get(sandbox_id)
                if lease is None:
                    # Never destroy a sandbox whose lease is alive: it may belong
                    # to a Run running right now. An instance with no lease record
                    # at all is only reclaimed once it is older than a full lease
                    # TTL, which outlasts the window between creating a sandbox
                    # and recording its lease.
                    created = by_id[sandbox_id].created_at
                    if created is None or now - created < grace:
                        continue
                    logger.warning(
                        "reclaiming an untracked sandbox with no lease record",
                        extra={"sandbox_id": sandbox_id},
                    )
                    if await self._destroy(sandbox_id):
                        reclaimed.append(sandbox_id)
                    continue
                if not callable(reclaim_one):
                    continue
                if not await self._destroy(sandbox_id):
                    continue
                await self._leases.mark_reclaimed(lease.tenant_id, lease.lease_id)
                reclaimed.append(sandbox_id)
            live_by_sandbox = {lease.sandbox_id: lease for lease in owned}
            for sandbox_id in missing:
                lease = live_by_sandbox[sandbox_id]
                await self._leases.mark_reclaimed(lease.tenant_id, lease.lease_id)
            if reclaimed and self._metrics is not None:
                self._metrics.increment(
                    "harness_sandbox_orphans_reclaimed_total", len(reclaimed)
                )

        if self._metrics is not None:
            self._metrics.gauge("harness_sandbox_live_leases", len(live))
            self._metrics.gauge("harness_sandbox_platform_instances", len(instances))

        return report.model_copy(
            update={
                "untracked": untracked,
                "missing": missing,
                "reclaimed": tuple(reclaimed),
            }
        )
