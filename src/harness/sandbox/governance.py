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
        provider_name: str | None = None,
        clock: Any | None = None,
        metrics: Any | None = None,
    ) -> None:
        self._leases = leases
        self._provider = provider
        # A container that never owns the platform client (the API process) still
        # has to name the backend its leases belong to.
        self._provider_name = provider_name
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
        provider_name = self._provider_name or str(
            getattr(self._provider, "provider_name", "unknown")
        )
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

        # Ownership is matched on the Run the platform recorded in the sandbox
        # metadata, not on the sandbox id: a deferred provider hands out its own
        # local handle id, so the id a lease stores is not the platform's.
        live_runs = {lease.run_id for lease in live}
        owned_by_run = {
            instance.sandbox_id
            for instance in instances
            if instance.metadata.get("harness.run") in live_runs
        }
        present = {instance.sandbox_id for instance in instances}
        tracked = {lease.sandbox_id for lease in live} | owned_by_run
        untracked = tuple(sorted(present - tracked))
        missing = tuple(sorted({lease.sandbox_id for lease in owned} - present))

        reclaimed: list[str] = []
        if reclaim:
            expired = await self._leases.expired()
            by_id = {instance.sandbox_id: instance for instance in instances}
            grace = timedelta(seconds=self._lease_ttl)
            for sandbox_id in untracked:
                instance = by_id[sandbox_id]
                run_id = instance.metadata.get("harness.run")
                lease = next(
                    (item for item in expired if item.run_id == run_id), None
                ) if run_id else None
                if lease is None and run_id is not None:
                    # Labelled with a Run whose lease has not expired: that
                    # Execution may still be running, so it is not ours to end.
                    continue
                if lease is None:
                    # No Run label at all, so age is the only evidence: reclaim
                    # it once it outlives a full lease TTL, longer than the gap
                    # between creating a sandbox and recording its lease.
                    created = instance.created_at
                    if created is None or now - created < grace:
                        continue
                    logger.warning(
                        "reclaiming an untracked sandbox with no lease record",
                        extra={"sandbox_id": sandbox_id},
                    )
                if await self._destroy(sandbox_id):
                    reclaimed.append(sandbox_id)
                    if lease is not None:
                        await self._leases.mark_reclaimed(
                            lease.tenant_id, lease.lease_id
                        )
            owned_by_sandbox = {lease.sandbox_id: lease for lease in owned}
            for sandbox_id in missing:
                lease = owned_by_sandbox[sandbox_id]
                await self._leases.mark_reclaimed(lease.tenant_id, lease.lease_id)

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
