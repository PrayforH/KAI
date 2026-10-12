"""Atomic publication, revision CAS and action reservation ports."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Protocol

from harness.core.errors import ConflictError, NotFoundError
from harness.presentation.models import ActionRequest, Publication, ViewState
from harness.presentation.schema import Presentation


class PresentationRepository(Protocol):
    async def publish(self, publication: Publication) -> Publication: ...
    async def for_run(self, tenant_id: str, run_id: str) -> Publication | None: ...
    async def get(
        self, tenant_id: str, presentation_id: str, revision: int | None = None
    ) -> tuple[Publication, Presentation]: ...
    async def revise(
        self,
        tenant_id: str,
        presentation_id: str,
        expected_revision: int,
        presentation: Presentation,
    ) -> Presentation: ...
    async def reserve(self, request: ActionRequest, *, single_use: bool) -> ActionRequest: ...
    async def requests_for_presentation(
        self, tenant_id: str, actor_user_id: str, presentation_id: str
    ) -> list[ActionRequest]: ...
    async def request(self, tenant_id: str, request_id: str) -> ActionRequest: ...
    async def pending(self, now: datetime, *, limit: int = 100) -> list[ActionRequest]: ...
    async def claim(self, request: ActionRequest, now: datetime) -> ActionRequest | None: ...
    async def save_request(self, request: ActionRequest, expected_fencing: int) -> bool: ...
    async def view(
        self, tenant_id: str, user_id: str, presentation_id: str, revision: int
    ) -> ViewState: ...
    async def save_view(
        self,
        tenant_id: str,
        user_id: str,
        presentation_id: str,
        revision: int,
        expected_version: int,
        state: dict[str, Any],
    ) -> ViewState: ...
    async def outbox(self, *, limit: int = 100) -> list[dict[str, Any]]: ...
    async def acknowledge(self, outbox_id: int) -> None: ...


def same_request(existing: ActionRequest, request: ActionRequest) -> ActionRequest:
    if existing.payload_hash != request.payload_hash:
        raise ConflictError("idempotency key or submitted action has different input")
    return existing


def ui_event(
    publication: Publication, event_type: str, key: str, payload: dict[str, Any]
) -> dict[str, Any]:
    return {
        "event_id": key,
        "tenant_id": publication.tenant_id,
        "run_id": publication.run_id,
        "session_id": publication.session_id,
        "event_type": event_type,
        "timestamp": publication.created_at.isoformat(),
        "payload": payload,
    }


class InMemoryPresentationRepository:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._publications: dict[tuple[str, str], Publication] = {}
        self._revisions: dict[tuple[str, str, int], Presentation] = {}
        self._owners: dict[tuple[str, str], Publication] = {}
        self._current: dict[tuple[str, str], int] = {}
        self._requests: dict[tuple[str, str], ActionRequest] = {}
        self._views: dict[tuple[str, str, str, int], ViewState] = {}
        self._outbox: dict[int, dict[str, Any]] = {}
        self._outbox_index = 0

    def _emit(self, event: dict[str, Any]) -> None:
        self._outbox_index += 1
        self._outbox[self._outbox_index] = {"outbox_id": self._outbox_index, **event}

    async def publish(self, publication: Publication) -> Publication:
        async with self._lock:
            key = (publication.tenant_id, publication.run_id)
            if key in self._publications:
                return self._publications[key]
            self._publications[key] = publication
            for p in publication.presentations:
                pid = (publication.tenant_id, p.presentation_id)
                self._owners[pid] = publication
                self._current[pid] = p.revision
                self._revisions[(*pid, p.revision)] = p
                self._emit(
                    ui_event(
                        publication,
                        "ui.presentation.ready",
                        p.presentation_id,
                        {
                            "presentation_id": p.presentation_id,
                            "revision": p.revision,
                            "kind": p.kind,
                            "snapshot_hash": p.snapshot_hash,
                        },
                    )
                )
            return publication

    async def for_run(self, tenant_id: str, run_id: str) -> Publication | None:
        return self._publications.get((tenant_id, run_id))

    async def get(
        self, tenant_id: str, presentation_id: str, revision: int | None = None
    ) -> tuple[Publication, Presentation]:
        key = (tenant_id, presentation_id)
        owner = self._owners.get(key)
        if owner is None:
            raise NotFoundError("presentation not found")
        p = self._revisions.get((*key, revision or self._current[key]))
        if p is None:
            raise NotFoundError("presentation revision not found")
        return owner, p

    async def revise(
        self,
        tenant_id: str,
        presentation_id: str,
        expected_revision: int,
        presentation: Presentation,
    ) -> Presentation:
        async with self._lock:
            owner, old = await self.get(tenant_id, presentation_id)
            if old.revision != expected_revision:
                raise ConflictError("presentation revision changed")
            if (
                presentation.presentation_id != presentation_id
                or presentation.run_id != old.run_id
                or presentation.revision != expected_revision + 1
            ):
                raise ConflictError("revision identity changed")
            key = (tenant_id, presentation_id)
            self._current[key] = presentation.revision
            self._revisions[(*key, presentation.revision)] = presentation
            self._emit(
                ui_event(
                    owner,
                    "ui.presentation.updated",
                    f"{presentation_id}-r{presentation.revision}",
                    {"presentation_id": presentation_id, "revision": presentation.revision},
                )
            )
            return presentation

    async def reserve(self, request: ActionRequest, *, single_use: bool) -> ActionRequest:
        async with self._lock:
            for existing in self._requests.values():
                if (
                    existing.tenant_id,
                    existing.actor_user_id,
                    existing.presentation_id,
                    existing.action_id,
                ) != (
                    request.tenant_id,
                    request.actor_user_id,
                    request.presentation_id,
                    request.action_id,
                ):
                    continue
                if existing.idempotency_key == request.idempotency_key or (
                    single_use and existing.revision == request.revision
                ):
                    return same_request(existing, request)
            owner, current = await self.get(request.tenant_id, request.presentation_id)
            if owner.owner_user_id != request.actor_user_id:
                raise NotFoundError("presentation not found")
            if current.revision != request.revision:
                raise ConflictError("presentation revision changed; refresh before submitting")
            if not any(a["action_id"] == request.action_id for a in current.actions):
                raise NotFoundError("action not found")
            self._requests[(request.tenant_id, request.request_id)] = request
            self._emit(
                ui_event(
                    owner,
                    "ui.action.requested",
                    request.request_id,
                    {
                        "request_id": request.request_id,
                        "presentation_id": request.presentation_id,
                        "action_id": request.action_id,
                        "revision": request.revision,
                    },
                )
            )
            return request

    async def requests_for_presentation(
        self, tenant_id: str, actor_user_id: str, presentation_id: str
    ) -> list[ActionRequest]:
        return [
            r
            for r in self._requests.values()
            if (r.tenant_id, r.actor_user_id, r.presentation_id)
            == (tenant_id, actor_user_id, presentation_id)
        ][-100:]

    async def request(self, tenant_id: str, request_id: str) -> ActionRequest:
        request = self._requests.get((tenant_id, request_id))
        if request is None:
            raise NotFoundError("action request not found")
        return request

    async def pending(self, now: datetime, *, limit: int = 100) -> list[ActionRequest]:
        candidates = [
            r
            for r in self._requests.values()
            if r.status == "accepted"
            or r.status == "submitted"
            or r.status == "dispatching"
            and r.lease_until
            and r.lease_until <= now
        ]
        return sorted(candidates, key=lambda r: (r.status == "submitted", r.updated_at))[:limit]

    async def claim(self, request: ActionRequest, now: datetime) -> ActionRequest | None:
        async with self._lock:
            current = await self.request(request.tenant_id, request.request_id)
            if current.fencing_token != request.fencing_token or current.status not in {
                "accepted",
                "dispatching",
            }:
                return None
            if (
                current.status == "dispatching"
                and current.lease_until
                and current.lease_until > now
            ):
                return None
            claimed = current.model_copy(
                update={
                    "status": "dispatching",
                    "fencing_token": current.fencing_token + 1,
                    "lease_until": now + timedelta(seconds=30),
                    "updated_at": now,
                }
            )
            self._requests[(request.tenant_id, request.request_id)] = claimed
            return claimed

    async def save_request(self, request: ActionRequest, expected_fencing: int) -> bool:
        async with self._lock:
            current = await self.request(request.tenant_id, request.request_id)
            if current.fencing_token != expected_fencing or current.status not in {
                "dispatching",
                "submitted",
            }:
                return False
            self._requests[(request.tenant_id, request.request_id)] = request
            if request.status not in {"dispatching", "submitted"}:
                owner, _ = await self.get(request.tenant_id, request.presentation_id)
                self._emit(
                    ui_event(
                        owner,
                        "ui.action.completed",
                        request.request_id + "-completed",
                        {
                            "request_id": request.request_id,
                            "status": request.status,
                            "run_id": request.run_id,
                        },
                    )
                )
            return True

    async def view(
        self, tenant_id: str, user_id: str, presentation_id: str, revision: int
    ) -> ViewState:
        return self._views.get((tenant_id, user_id, presentation_id, revision), ViewState())

    async def save_view(
        self,
        tenant_id: str,
        user_id: str,
        presentation_id: str,
        revision: int,
        expected_version: int,
        state: dict[str, Any],
    ) -> ViewState:
        async with self._lock:
            current = await self.view(tenant_id, user_id, presentation_id, revision)
            if current.version != expected_version:
                raise ConflictError("view state changed")
            result = ViewState(version=current.version + 1, state=state)
            self._views[(tenant_id, user_id, presentation_id, revision)] = result
            return result

    async def outbox(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return list(self._outbox.values())[:limit]

    async def acknowledge(self, outbox_id: int) -> None:
        self._outbox.pop(outbox_id, None)
