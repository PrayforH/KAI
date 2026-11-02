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
        current = await self.runs.get(self.run.tenant_id, self.run.run_id)
        if (
            current.fencing_token != self.run.fencing_token
            or current.status is not RunStatus.RUNNING
        ):
            return
        await self.events.append(
            tenant_id=self.run.tenant_id,
            run_id=self.run.run_id,
            session_id=self.run.session_id,
            event_type=event_type,
            payload=payload,
        )

    async def open(self) -> None:
        # A new runtime activation must not silently inherit a transport write
        # whose consumer never acknowledged it. Keep the text; never resend it.
        events = await self.events.list_after(self.run.tenant_id, self.run.run_id, 0)
        latest: dict[str, str] = {}
        texts: dict[str, str] = {}
        for event in events:
            if event.type.startswith("run.steer."):
                key = str(event.payload.get("request_id", ""))
                if event.type == "run.steer.requested":
                    texts.setdefault(key, str(event.payload.get("text", "")))
                    if key in latest:
                        continue
                latest[key] = event.type
        for request_id, status in latest.items():
            if request_id and status == "run.steer.sending":
                await self.emit("run.steer.unknown", {
                    "request_id": request_id,
                    "text": texts.get(request_id, ""),
                    "error": "运行已恢复，但未确认是否收到这条补充；请核对原任务后决定是否重发。",
                })
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
            if event.type in {
                "run.steer.sending", "run.steer.accepted", "run.steer.failed", "run.steer.unknown"
            }:
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
