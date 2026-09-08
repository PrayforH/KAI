"""Durable, per-run inbox shared by API and independently hosted Workers."""

from dataclasses import dataclass

from harness.application.events import EventService
from harness.core.models import Run, RunStatus
from harness.core.ports import RunRepository


@dataclass(frozen=True)
class SteeringInput:
    request_id: str
    text: str


class SteeringInbox:
    def __init__(self, run: Run, runs: RunRepository, events: EventService) -> None:
        self.run = run
        self.runs = runs
        self.events = events
        self.cursor = 0
        self.seen: set[str] = set()
        self.closed = False

    async def emit(self, event_type: str, payload: dict[str, object]) -> None:
        await self.events.append(
            tenant_id=self.run.tenant_id,
            run_id=self.run.run_id,
            session_id=self.run.session_id,
            event_type=event_type,
            payload=payload,
        )

    async def open(self) -> None:
        await self.emit("runtime.steering.ready", {})

    async def read(self) -> list[SteeringInput]:
        if self.closed:
            return []
        current = await self.runs.get(self.run.tenant_id, self.run.run_id)
        if (
            current.status is not RunStatus.RUNNING
            or current.fencing_token != self.run.fencing_token
        ):
            return []
        events = await self.events.list_after(self.run.tenant_id, self.run.run_id, self.cursor)
        # Never replay a transport write after a Worker crash: delivery can be uncertain.
        for event in events:
            if event.type in {"run.steer.sending", "run.steer.accepted", "run.steer.failed"}:
                self.seen.add(str(event.payload.get("request_id", "")))
        inputs: list[SteeringInput] = []
        for event in events:
            self.cursor = max(self.cursor, event.sequence)
            if event.type != "run.steer.requested":
                continue
            request_id = str(event.payload.get("request_id", ""))
            if request_id in self.seen:
                continue
            self.seen.add(request_id)
            inputs.append(SteeringInput(request_id, str(event.payload.get("text", ""))))
        return inputs

    async def sending(self, item: SteeringInput) -> None:
        await self.emit("run.steer.sending", {"request_id": item.request_id})

    async def acknowledge(self, item: SteeringInput, *, error: str | None = None) -> None:
        await self.emit(
            "run.steer.failed" if error else "run.steer.accepted",
            {
                "request_id": item.request_id,
                "text": item.text,
                **({"error": error} if error else {}),
            },
        )

    async def close(self) -> None:
        self.closed = True
        await self.emit("runtime.steering.closed", {})
