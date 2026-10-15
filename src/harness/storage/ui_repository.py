"""UI admission serialized on its presentation row, across API processes.

Snapshots and their outbox notices commit together. A dispatcher can retry
queue delivery after a crash without creating a second logical Run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import and_, case, false, or_, select, update
from sqlalchemy.engine import CursorResult

from harness.core.errors import ConflictError, NotFoundError
from harness.presentation.models import ActionRequest, Publication, ViewState
from harness.presentation.repositories import same_request, ui_event
from harness.presentation.schema import Presentation
from harness.storage.database import SessionFactory
from harness.storage.models import OutboxRow, RunRow
from harness.storage.ui_models import (
    UiActionRequestRow,
    UiPresentationRow,
    UiPublicationRow,
    UiRevisionRow,
    UiViewStateRow,
)

_TOPIC = "harness.ui.event"


def _outbox(event: dict[str, Any]) -> OutboxRow:
    return OutboxRow(
        topic=_TOPIC,
        aggregate_id=event["run_id"],
        payload=event,
        created_at=datetime.fromisoformat(event["timestamp"]),
        published_at=None,
    )


def _request_row(r: ActionRequest) -> UiActionRequestRow:
    return UiActionRequestRow(
        tenant_id=r.tenant_id,
        request_id=r.request_id,
        actor_user_id=r.actor_user_id,
        presentation_id=r.presentation_id,
        action_id=r.action_id,
        revision=r.revision,
        idempotency_key=r.idempotency_key,
        status=r.status,
        fencing_token=r.fencing_token,
        lease_until=r.lease_until,
        updated_at=r.updated_at,
        payload=r.model_dump(mode="json"),
    )


class PostgresPresentationRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def publish(self, publication: Publication) -> Publication:
        async with self._sessions() as s, s.begin():
            # The run lock arbitrates first publication and rejects old fences.
            run = await s.get(
                RunRow, (publication.tenant_id, publication.run_id), with_for_update=True
            )
            if run is None:
                raise NotFoundError("run not found")
            existing = await s.get(UiPublicationRow, (publication.tenant_id, publication.run_id))
            if existing:
                return Publication.model_validate(existing.payload)
            if run.fencing_token != publication.fencing_token or run.status not in {
                "succeeded",
                "failed",
                "rejected",
                "timed_out",
            }:
                raise ConflictError("UI publication requires the current terminal run fence")
            s.add(
                UiPublicationRow(
                    tenant_id=publication.tenant_id,
                    run_id=publication.run_id,
                    payload=publication.model_dump(mode="json"),
                )
            )
            for p in publication.presentations:
                s.add(
                    UiPresentationRow(
                        tenant_id=publication.tenant_id,
                        presentation_id=p.presentation_id,
                        run_id=publication.run_id,
                        current_revision=p.revision,
                    )
                )
                s.add(
                    UiRevisionRow(
                        tenant_id=publication.tenant_id,
                        presentation_id=p.presentation_id,
                        revision=p.revision,
                        payload=p.model_dump(mode="json"),
                    )
                )
                s.add(
                    _outbox(
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
                )
            return publication

    async def for_run(self, tenant_id: str, run_id: str) -> Publication | None:
        async with self._sessions() as s:
            row = await s.get(UiPublicationRow, (tenant_id, run_id))
            return Publication.model_validate(row.payload) if row else None

    async def get(
        self, tenant_id: str, presentation_id: str, revision: int | None = None
    ) -> tuple[Publication, Presentation]:
        async with self._sessions() as s:
            current = await s.get(UiPresentationRow, (tenant_id, presentation_id))
            if current is None:
                raise NotFoundError("presentation not found")
            owner = await s.get(UiPublicationRow, (tenant_id, current.run_id))
            row = await s.get(
                UiRevisionRow, (tenant_id, presentation_id, revision or current.current_revision)
            )
            if row is None or owner is None:
                raise NotFoundError("presentation revision not found")
            return Publication.model_validate(owner.payload), Presentation.model_validate(
                row.payload
            )

    async def revise(
        self,
        tenant_id: str,
        presentation_id: str,
        expected_revision: int,
        presentation: Presentation,
    ) -> Presentation:
        async with self._sessions() as s, s.begin():
            row = await s.get(UiPresentationRow, (tenant_id, presentation_id), with_for_update=True)
            if row is None:
                raise NotFoundError("presentation not found")
            if row.current_revision != expected_revision:
                raise ConflictError("presentation revision changed")
            if (
                presentation.presentation_id != presentation_id
                or presentation.run_id != row.run_id
                or presentation.revision != expected_revision + 1
            ):
                raise ConflictError("revision identity changed")
            row.current_revision = presentation.revision
            s.add(
                UiRevisionRow(
                    tenant_id=tenant_id,
                    presentation_id=presentation_id,
                    revision=presentation.revision,
                    payload=presentation.model_dump(mode="json"),
                )
            )
            owner = await s.get(UiPublicationRow, (tenant_id, row.run_id))
            assert owner is not None
            s.add(
                _outbox(
                    ui_event(
                        Publication.model_validate(owner.payload),
                        "ui.presentation.updated",
                        f"{presentation_id}-r{presentation.revision}",
                        {"presentation_id": presentation_id, "revision": presentation.revision},
                    )
                )
            )
            return presentation

    async def reserve(self, request: ActionRequest, *, single_use: bool) -> ActionRequest:
        async with self._sessions() as s, s.begin():
            row = await s.get(
                UiPresentationRow,
                (request.tenant_id, request.presentation_id),
                with_for_update=True,
            )
            if row is None:
                raise NotFoundError("presentation not found")
            statement = select(UiActionRequestRow).where(
                UiActionRequestRow.tenant_id == request.tenant_id,
                UiActionRequestRow.actor_user_id == request.actor_user_id,
                UiActionRequestRow.presentation_id == request.presentation_id,
                UiActionRequestRow.action_id == request.action_id,
                or_(
                    UiActionRequestRow.idempotency_key == request.idempotency_key,
                    UiActionRequestRow.revision == request.revision if single_use else false(),
                ),
            )
            existing = (await s.execute(statement)).scalar_one_or_none()
            if existing:
                return same_request(ActionRequest.model_validate(existing.payload), request)
            if row.current_revision != request.revision:
                raise ConflictError("presentation revision changed; refresh before submitting")
            revision = await s.get(
                UiRevisionRow, (request.tenant_id, request.presentation_id, request.revision)
            )
            assert revision is not None
            p = Presentation.model_validate(revision.payload)
            if not any(a["action_id"] == request.action_id for a in p.actions):
                raise NotFoundError("action not found")
            owner = await s.get(UiPublicationRow, (request.tenant_id, row.run_id))
            assert owner is not None
            publication = Publication.model_validate(owner.payload)
            if publication.owner_user_id != request.actor_user_id:
                raise NotFoundError("presentation not found")
            s.add(_request_row(request))
            event = ui_event(
                publication,
                "ui.action.requested",
                request.request_id,
                {
                    "request_id": request.request_id,
                    "presentation_id": request.presentation_id,
                    "action_id": request.action_id,
                    "revision": request.revision,
                },
            )
            event["timestamp"] = request.created_at.isoformat()
            s.add(_outbox(event))
            return request

    async def requests_for_presentation(
        self, tenant_id: str, actor_user_id: str, presentation_id: str
    ) -> list[ActionRequest]:
        statement = (
            select(UiActionRequestRow)
            .where(
                UiActionRequestRow.tenant_id == tenant_id,
                UiActionRequestRow.actor_user_id == actor_user_id,
                UiActionRequestRow.presentation_id == presentation_id,
            )
            .order_by(UiActionRequestRow.updated_at.desc())
            .limit(100)
        )
        async with self._sessions() as s:
            return [
                ActionRequest.model_validate(r.payload)
                for r in (await s.execute(statement)).scalars()
            ]

    async def request(self, tenant_id: str, request_id: str) -> ActionRequest:
        async with self._sessions() as s:
            row = await s.get(UiActionRequestRow, (tenant_id, request_id))
            if row is None:
                raise NotFoundError("action request not found")
            return ActionRequest.model_validate(row.payload)

    async def pending(self, now: datetime, *, limit: int = 100) -> list[ActionRequest]:
        statement = (
            select(UiActionRequestRow)
            .where(
                or_(
                    UiActionRequestRow.status.in_(["accepted", "submitted"]),
                    and_(
                        UiActionRequestRow.status == "dispatching",
                        UiActionRequestRow.lease_until <= now,
                    ),
                )
            )
            .order_by(
                case((UiActionRequestRow.status == "submitted", 1), else_=0),
                UiActionRequestRow.updated_at,
            )
            .limit(limit)
        )
        async with self._sessions() as s:
            return [
                ActionRequest.model_validate(row.payload)
                for row in (await s.execute(statement)).scalars()
            ]

    async def claim(self, request: ActionRequest, now: datetime) -> ActionRequest | None:
        claimed = request.model_copy(
            update={
                "status": "dispatching",
                "fencing_token": request.fencing_token + 1,
                "lease_until": now + timedelta(seconds=30),
                "updated_at": now,
            }
        )
        statement = (
            update(UiActionRequestRow)
            .where(
                UiActionRequestRow.tenant_id == request.tenant_id,
                UiActionRequestRow.request_id == request.request_id,
                UiActionRequestRow.fencing_token == request.fencing_token,
                or_(
                    UiActionRequestRow.status == "accepted",
                    and_(
                        UiActionRequestRow.status == "dispatching",
                        UiActionRequestRow.lease_until <= now,
                    ),
                ),
            )
            .values(
                status=claimed.status,
                fencing_token=claimed.fencing_token,
                lease_until=claimed.lease_until,
                updated_at=claimed.updated_at,
                payload=claimed.model_dump(mode="json"),
            )
        )
        async with self._sessions() as s, s.begin():
            result = cast(CursorResult[Any], await s.execute(statement))
            return claimed if result.rowcount else None

    async def save_request(self, request: ActionRequest, expected_fencing: int) -> bool:
        async with self._sessions() as s, s.begin():
            row = await s.get(
                UiActionRequestRow, (request.tenant_id, request.request_id), with_for_update=True
            )
            if (
                row is None
                or row.fencing_token != expected_fencing
                or row.status not in {"dispatching", "submitted"}
            ):
                return False
            row.status = request.status
            row.lease_until = request.lease_until
            row.updated_at = request.updated_at
            row.payload = request.model_dump(mode="json")
            if request.status not in {"dispatching", "submitted"}:
                p = await s.get(UiPresentationRow, (request.tenant_id, request.presentation_id))
                assert p is not None
                owner = await s.get(UiPublicationRow, (request.tenant_id, p.run_id))
                assert owner is not None
                event = ui_event(
                    Publication.model_validate(owner.payload),
                    "ui.action.completed",
                    request.request_id + "-completed",
                    {
                        "request_id": request.request_id,
                        "status": request.status,
                        "run_id": request.run_id,
                    },
                )
                event["timestamp"] = request.updated_at.isoformat()
                s.add(_outbox(event))
            return True

    async def view(
        self, tenant_id: str, user_id: str, presentation_id: str, revision: int
    ) -> ViewState:
        async with self._sessions() as s:
            row = await s.get(UiViewStateRow, (tenant_id, user_id, presentation_id, revision))
            return ViewState(version=row.version, state=row.payload) if row else ViewState()

    async def save_view(
        self,
        tenant_id: str,
        user_id: str,
        presentation_id: str,
        revision: int,
        expected_version: int,
        state: dict[str, Any],
    ) -> ViewState:
        async with self._sessions() as s, s.begin():
            # Serialize the absent-row case as well as updates.
            p = await s.get(UiPresentationRow, (tenant_id, presentation_id), with_for_update=True)
            if p is None:
                raise NotFoundError("presentation not found")
            row = await s.get(UiViewStateRow, (tenant_id, user_id, presentation_id, revision))
            if (row.version if row else 0) != expected_version:
                raise ConflictError("view state changed")
            if row:
                row.version += 1
                row.payload = state
            else:
                row = UiViewStateRow(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    presentation_id=presentation_id,
                    revision=revision,
                    version=1,
                    payload=state,
                )
                s.add(row)
            return ViewState(version=row.version, state=state)

    async def outbox(self, *, limit: int = 100) -> list[dict[str, Any]]:
        statement = (
            select(OutboxRow)
            .where(OutboxRow.topic == _TOPIC, OutboxRow.published_at.is_(None))
            .order_by(OutboxRow.outbox_id)
            .limit(limit)
        )
        async with self._sessions() as s:
            return [
                {"outbox_id": r.outbox_id, **r.payload}
                for r in (await s.execute(statement)).scalars()
            ]

    async def acknowledge(self, outbox_id: int) -> None:
        async with self._sessions() as s, s.begin():
            await s.execute(
                update(OutboxRow)
                .where(OutboxRow.outbox_id == outbox_id, OutboxRow.topic == _TOPIC)
                .values(published_at=datetime.now(UTC))
            )
