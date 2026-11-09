"""Dedicated internal Computer control service; never exposed by the user sandbox."""

import asyncio
import contextlib
import hmac
import json
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import and_, func, select
from sqlalchemy.engine import make_url

from harness.computer.access import CanonicalComputerAccess
from harness.computer.control import (
    MAX_BYTES,
    ComputerControlRouter,
    ComputerLifecycleRouter,
    ControlAction,
    DockerComputerControl,
)
from harness.computer.desktop import (
    DRAIN_POLL_SECONDS,
    DesktopControlService,
    DesktopOccupiedError,
    DesktopState,
)
from harness.computer.desktop_registry import DesktopRegistry
from harness.computer.host import ComputerLimits, DockerComputerLifecycle
from harness.computer.models import (
    AssistantComputer,
    ComputerLease,
    ComputerLeaseLostError,
    ComputerStartRejectedError,
    LeasePurpose,
)
from harness.computer.registry import ComputerRegistry
from harness.computer.remote import PROVIDER as REMOTE_PROVIDER
from harness.computer.remote import (
    RemoteComputerControl,
    RemoteComputerLifecycle,
    RemoteComputerSpec,
)
from harness.computer.storage import AssistantComputerRow, ComputerDesktopLeaseRow
from harness.computer.vm import PROVIDER as VM_PROVIDER
from harness.computer.vm_host import (
    GuestGate,
    VmComputerControl,
    VmComputerLifecycle,
    VmComputerSpec,
    VmDesktopService,
    verify_vm_instance,
)
from harness.core.errors import NotFoundError
from harness.storage.database import create_database


class ControllerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_COMPUTER_CONTROL_")
    database_url: str
    secret: SecretStr
    image: str
    authorization: Literal["canonical", "probe"] = "canonical"
    owners: dict[str, frozenset[str]] = Field(default_factory=dict)
    memory_bytes: int = 4 * 1024**3
    nano_cpus: int = 2_000_000_000
    docker_socket: str = "/var/run/docker.sock"
    # Node-private declarations for adopted QEMU computers, keyed by work disk.
    vm_computers: list[VmComputerSpec] = Field(default_factory=list[VmComputerSpec])
    remote_computers: list[RemoteComputerSpec] = Field(default_factory=list[RemoteComputerSpec])
    port: int = 8840


class ControlRequest(BaseModel):
    token: ComputerLease
    action: ControlAction
    payload: dict[str, Any]


class LifecycleRequest(BaseModel):
    token: ComputerLease
    operation: str
    image: str | None = None


class ImageCheckRequest(BaseModel):
    image: str
    provider: str | None = None


class DesktopSlotRequest(BaseModel):
    """Bootstrap/maintenance access to one Bot slot, without an execution lease.

    Only the private node holds this endpoint's secret: the request names the
    owner and a pre-validated slot descriptor, and the guest gate validates the
    descriptor again before it touches a unit.
    """

    tenant_id: str
    user_id: str
    operation: Literal["ensure", "stop", "status"]
    payload: dict[str, Any]


class DesktopRequest(BaseModel):
    token: ComputerLease
    operation: str
    payload: dict[str, Any]
    expected_epoch: int


async def _active_execution(db: Any, computer: AssistantComputerRow) -> bool:
    """Whether a Run still holds this computer's execution lease."""

    from harness.computer.storage import ComputerLeaseRow

    count = await db.scalar(
        select(func.count())
        .select_from(ComputerLeaseRow)
        .where(
            ComputerLeaseRow.tenant_id == computer.tenant_id,
            ComputerLeaseRow.user_id == computer.user_id,
            ComputerLeaseRow.state == "active",
            ComputerLeaseRow.run_id.is_not(None),
            ComputerLeaseRow.expires_at > func.clock_timestamp(),
        )
    )
    return bool(count)


async def drain_pending_takeovers(
    desktop_leases: DesktopControlService,
    screen: VmDesktopService,
    sessions: Any,
    *,
    limit: int = 8,
) -> int:
    """Stop and drain this node's actions for every pending takeover.

    The desktop lease row is the authority: the node may publish human
    ownership only for a drain it actually performed, and a pending window
    that expires first falls back to observation through the lease itself.
    Returns how many pending takeovers were confirmed.
    """

    served = screen.served
    async with sessions() as db:
        statement = (
            select(ComputerDesktopLeaseRow, AssistantComputerRow)
            .join(
                AssistantComputerRow,
                and_(
                    AssistantComputerRow.tenant_id == ComputerDesktopLeaseRow.tenant_id,
                    AssistantComputerRow.user_id == ComputerDesktopLeaseRow.user_id,
                ),
            )
            .where(
                ComputerDesktopLeaseRow.state == DesktopState.TAKEOVER_PENDING.value,
                ComputerDesktopLeaseRow.expires_at > func.clock_timestamp(),
                AssistantComputerRow.provider.in_((VM_PROVIDER, REMOTE_PROVIDER)),
            )
            .order_by(ComputerDesktopLeaseRow.expires_at)
            .limit(limit)
        )
        if served:
            statement = statement.where(AssistantComputerRow.volume_ref.in_(served))
        rows = (await db.execute(statement)).all()

    async def drain(lease: ComputerDesktopLeaseRow, computer: AssistantComputerRow) -> bool:
        remaining = (
            (lease.expires_at - datetime.now(UTC)).total_seconds()
            if lease.expires_at is not None
            else 0
        )
        if remaining <= 0:
            return False
        slot = {"desktop_id": lease.desktop_id} if lease.desktop_id != "vm" else {}
        if computer.volume_ref in served:
            screen.cancel(computer.volume_ref, **slot)
            if not await screen.wait_drained(
                computer.volume_ref, timeout=remaining, expected_epoch=lease.epoch, **slot
            ):
                return False
        else:
            if slot:
                return False
            async with sessions() as db:
                if await _active_execution(db, computer):
                    return False
        try:
            state = await desktop_leases.confirm_human(
                lease.tenant_id, lease.user_id, expected_epoch=lease.epoch, **slot
            )
        except NotFoundError:
            return False
        return state.state == DesktopState.HUMAN.value and state.epoch == lease.epoch + 1

    return sum(await asyncio.gather(*(drain(lease, computer) for lease, computer in rows)))


def create_app(settings: ControllerSettings) -> FastAPI:
    secret = settings.secret.get_secret_value()
    if len(secret) < 32:
        raise ValueError("computer controller requires a private authentication secret")
    if (
        settings.authorization == "probe"
        and make_url(settings.database_url).database != "harness_computer_probe"
    ):
        raise ValueError("probe authorization requires the isolated Computer probe database")
    if len({spec.volume_ref for spec in settings.vm_computers}) != len(settings.vm_computers):
        raise ValueError("computer VM declarations must use distinct work disks")
    bot_disks = [spec.bot_work_source for spec in settings.vm_computers if spec.bot_work_source]
    if len(set(bot_disks)) != len(bot_disks):
        raise ValueError("computer VM declarations must use distinct Bot workdisks")
    engine, sessions = create_database(settings.database_url)
    access = (
        CanonicalComputerAccess(sessions, settings.owners)
        if settings.authorization == "canonical"
        else None
    )
    docker = httpx.AsyncClient(
        base_url="http://docker",
        transport=httpx.AsyncHTTPTransport(uds=settings.docker_socket),
        trust_env=False,
        timeout=30,
    )
    registry = ComputerRegistry(sessions)
    vm_specs = {spec.volume_ref: spec for spec in settings.vm_computers}
    remote_specs = {spec.volume_ref: spec for spec in settings.remote_computers}
    if len(remote_specs) != len(settings.remote_computers) or vm_specs.keys() & remote_specs.keys():
        raise ValueError("computer declarations require distinct target workspaces")
    controller = ComputerControlRouter(
        registry,
        {
            "docker-computer": DockerComputerControl(registry, docker, authorizer=access),
            REMOTE_PROVIDER: RemoteComputerControl(registry, remote_specs, authorizer=access),
            **(
                {VM_PROVIDER: VmComputerControl(
                    registry, vm_specs, authorizer=access, desktops=DesktopRegistry(sessions)
                )}
                if vm_specs
                else {}
            ),
        },
    )
    desktop_service = DesktopControlService(sessions)
    desktop_screen = VmDesktopService({**vm_specs, **remote_specs})
    host = ComputerLifecycleRouter(
        registry,
        {
            REMOTE_PROVIDER: RemoteComputerLifecycle(registry, remote_specs, authorizer=access),
            "docker-computer": DockerComputerLifecycle(
                controller.registry,
                docker,
                ComputerLimits(
                    settings.image, memory_bytes=settings.memory_bytes, nano_cpus=settings.nano_cpus
                ),
                authorizer=access,
            ),
            **(
                {VM_PROVIDER: VmComputerLifecycle(registry, docker, vm_specs, authorizer=access)}
                if vm_specs
                else {}
            ),
        },
    )

    def desktop_guard(token: ComputerLease, expected_epoch: int):
        """The one authorization rule for desktop actions, pre-check and re-check."""

        async def guard() -> AssistantComputer:
            computer, current = await registry.holder(token)
            if computer.provider not in {VM_PROVIDER, REMOTE_PROVIDER}:
                raise ValueError("this computer has no desktop control surface")
            if current.purpose is not LeasePurpose.EXECUTE:
                raise ComputerLeaseLostError("desktop actions need a live execution lease")
            if access is not None:
                await access.authorize(computer, current, "exec")
            # Renew-only: the worker acquires explicitly; the gate never
            # grants input ownership from observation by itself.
            state = await desktop_service.renew_agent(
                computer.tenant_id, computer.user_id, owner=current.owner,
                desktop_id=current.desktop_id,
            )
            # The acquired epoch is authoritative: a takeover bumped it, so a
            # caller still quoting its old epoch is fenced here.
            if state.state != "agent" or state.owner != current.owner:
                raise ComputerLeaseLostError("desktop control was not granted to this holder")
            if expected_epoch != state.epoch:
                raise ComputerLeaseLostError("desktop control epoch is stale")
            return computer

        return guard

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        watcher = asyncio.create_task(takeover_drain())
        try:
            yield
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
            await desktop_screen.close()
            await docker.aclose()
            await engine.dispose()

    async def takeover_drain() -> None:
        while True:
            try:
                await drain_pending_takeovers(desktop_service, desktop_screen, sessions)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).warning("computer takeover drain failed", exc_info=True)
            await asyncio.sleep(DRAIN_POLL_SECONDS)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # Exposed for test doubles only; production code goes through the routes.
    app.state.desktop_service = desktop_service
    app.state.desktop_screen = desktop_screen
    app.state.registry = registry

    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_api_route("/health", health, methods=["GET"])

    async def private_body(request: Request) -> bytearray:
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + secret).encode()):
            raise HTTPException(403, "Computer control is not authorized")
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > MAX_BYTES:
                raise HTTPException(413, "Computer control request is too large")
        return body

    async def control(request: Request):
        try:
            command = ControlRequest.model_validate_json(await private_body(request))
        except ValidationError:
            raise HTTPException(400, "Computer control request is invalid") from None
        try:
            return await controller.call(command.token, command.action, command.payload)
        except (ComputerLeaseLostError, NotFoundError) as error:
            logging.getLogger(__name__).warning(
                "computer control refused: %s: %s", type(error).__name__, str(error)[:200]
            )
            raise HTTPException(409, "Computer execution ownership was lost") from None
        except (httpx.HTTPError, RuntimeError, ValueError):
            # Never return Docker messages, request input, private paths or env.
            raise HTTPException(503, "Computer control could not confirm execution") from None

    app.add_api_route("/v1/computer/control", control, methods=["POST"])

    async def check_image(request: Request):
        try:
            command = ImageCheckRequest.model_validate_json(await private_body(request))
        except ValidationError:
            raise HTTPException(400, "Computer image request is invalid") from None
        try:
            await host.check_image(command.image, provider=command.provider)
            return {"available": True}
        except ComputerStartRejectedError:
            raise HTTPException(422, "Computer image is unavailable") from None
        except (httpx.HTTPError, RuntimeError, ValueError):
            raise HTTPException(503, "Computer image validation could not be confirmed") from None

    app.add_api_route("/v1/computer/image/check", check_image, methods=["POST"])

    async def lifecycle(request: Request):
        try:
            command = LifecycleRequest.model_validate_json(await private_body(request))
        except ValidationError:
            raise HTTPException(400, "Computer lifecycle request is invalid") from None
        try:
            return {
                "provider_ref": await host.lifecycle(
                    command.token, command.operation, image=command.image
                )
            }
        except ComputerStartRejectedError:
            raise HTTPException(422, "Computer image is unavailable") from None
        except (ComputerLeaseLostError, NotFoundError):
            raise HTTPException(409, "Computer execution ownership was lost") from None
        except (httpx.HTTPError, RuntimeError, ValueError):
            raise HTTPException(503, "Computer lifecycle could not be confirmed") from None

    app.add_api_route("/v1/computer/lifecycle", lifecycle, methods=["POST"])

    async def desktop(request: Request):
        try:
            command = DesktopRequest.model_validate_json(await private_body(request))
        except ValidationError:
            raise HTTPException(400, "Computer desktop request is invalid") from None
        try:
            screen: VmDesktopService = request.app.state.desktop_screen
            guard = desktop_guard(command.token, command.expected_epoch)
            computer = await guard()
            # operate re-runs the same guard inside the action lock: a queued
            # action is only executed while its lease and epoch still hold.
            return await screen.operate(
                computer,
                command.operation,
                command.payload,
                revalidate=guard,
                scope=command.token.lease_id,
                expected_epoch=command.expected_epoch,
                desktop_id=None if command.token.desktop_id == "vm" else command.token.desktop_id,
            )
        except (ComputerLeaseLostError, DesktopOccupiedError, NotFoundError):
            raise HTTPException(409, "Computer desktop ownership was lost") from None
        except (
            TimeoutError,
            httpx.HTTPError,
            RuntimeError,
            ValueError,
            OSError,
            EOFError,
        ) as error:
            # The client never sees provider detail; the operator needs one
            # line naming the failure class and a bounded message.
            logging.getLogger(__name__).warning(
                "computer desktop action failed: %s: %s",
                type(error).__name__,
                str(error)[:300],
            )
            raise HTTPException(503, "Computer desktop could not be reached") from None

    async def desktop_slot(request: Request):
        try:
            command = DesktopSlotRequest.model_validate_json(await private_body(request))
        except ValidationError:
            raise HTTPException(400, "Computer desktop slot request is invalid") from None
        try:
            computer = await registry.get(command.tenant_id, command.user_id)
            spec = vm_specs.get(computer.volume_ref)
            if spec is None or spec.guest_base_digest != computer.image:
                raise RuntimeError("computer VM is not declared on this node")
            provider_ref = None
            if command.operation == "ensure" and access is not None:
                found = await docker.get(f"/containers/{spec.container_name}/json")
                found.raise_for_status()
                info = found.json()
                provider_ref = verify_vm_instance(info, spec)
                if computer.provider_ref not in {None, provider_ref}:
                    raise RuntimeError("computer VM instance changed")
                if info.get("State", {}).get("Running") is not True:
                    raise RuntimeError("computer VM runner is not running")
            result = await GuestGate(spec).call(
                "desktop_session",
                {**command.payload, "op": command.operation},
                timeout_seconds=120,
            )
            if result.exit_code:
                raise RuntimeError("guest did not confirm desktop maintenance")
            answer = json.loads(result.stdout)
            if not isinstance(answer, dict) or answer.get("op") != command.operation:
                raise RuntimeError("guest desktop maintenance acknowledgement is invalid")
            desktop_id = command.payload.get("desktopId")
            if command.operation == "stop" and isinstance(desktop_id, str):
                await desktop_screen.close_slot(computer.volume_ref, desktop_id)
            return {
                "op": command.operation, "state": answer.get("state"),
                **({"provider_ref": provider_ref} if provider_ref is not None else {}),
            }
        except NotFoundError:
            raise HTTPException(404, "Computer is not registered") from None
        except (httpx.HTTPError, RuntimeError, ValueError, OSError, EOFError,
                ComputerLeaseLostError) as error:
            logging.getLogger(__name__).warning(
                "computer desktop slot failed: %s: %s", type(error).__name__, str(error)[:300]
            )
            raise HTTPException(503, "Computer desktop slot could not be reached") from None

    app.add_api_route("/v1/computer/desktop-slot", desktop_slot, methods=["POST"])
    app.add_api_route("/v1/computer/desktop", desktop, methods=["POST"])
    return app


def main() -> None:
    settings = ControllerSettings(
        database_url=os.environ["HARNESS_COMPUTER_CONTROL_DATABASE_URL"],
        secret=SecretStr(os.environ["HARNESS_COMPUTER_CONTROL_SECRET"]),
        image=os.environ["HARNESS_COMPUTER_CONTROL_IMAGE"],
    )
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
