"""Opt-in personal Computer routing and its private node transport."""

import re
from collections.abc import Awaitable, Callable

import httpx

from harness.computer.access import CanonicalComputerAccess
from harness.computer.acp_tools import computer_acp_overlay
from harness.computer.control import HttpComputerControl
from harness.computer.desktop import DesktopControlService
from harness.computer.desktop_registry import DesktopRegistry
from harness.computer.docker import DockerComputerDriver
from harness.computer.gui_tools import computer_gui_overlay
from harness.computer.models import AssistantComputer, DesktopState
from harness.computer.registry import ComputerRegistry
from harness.computer.remote import PROVIDER as REMOTE_PROVIDER
from harness.computer.remote import RemoteComputerDriver
from harness.computer.sandbox import ComputerSandboxProvider
from harness.computer.vm import PROVIDER as VM_PROVIDER
from harness.computer.vm import VmComputerDriver
from harness.config import Settings
from harness.core.errors import NotFoundError
from harness.core.models import Run, Session
from harness.runtime.platform_tools import PlatformToolOverlay
from harness.sandbox.base import SandboxHandle
from harness.storage.database import SessionFactory

DOCKER_PROVIDER = "docker-computer"
_IMAGE = r"(?:[A-Za-z0-9_./:-]+@)?sha256:[a-f0-9]{64}"


class ComputerRuntime:
    def __init__(
        self,
        settings: Settings,
        sessions: SessionFactory,
        *,
        execution_enabled: bool,
        specialist_for_session: (
            Callable[[str, str, str], Awaitable[str | None]] | None
        ) = None,
    ):
        self.enabled = bool(settings.assistant_computer_owners)
        self.access = CanonicalComputerAccess(sessions, settings.assistant_computer_owners)
        self.registry = ComputerRegistry(sessions)
        self.settings = settings
        self.client: httpx.AsyncClient | None = None
        self.drivers: dict[str, DockerComputerDriver] = {}
        self.desktop = DesktopControlService(sessions)
        self.desktops = DesktopRegistry(sessions)
        self.specialist_for_session = specialist_for_session
        self.per_bot_desktop = bool(settings.assistant_computer_per_bot_desktop)
        self.default_provider = DOCKER_PROVIDER
        if not execution_enabled or not self.enabled:
            return
        address = httpx.URL(settings.assistant_computer_control_url)
        image = settings.assistant_computer_image
        secret = settings.assistant_computer_control_secret.get_secret_value()
        vm_image = settings.assistant_computer_vm_image
        if (
            settings.sandbox_execution_mode != "worker_cli_deferred"
            or address.scheme not in {"http", "https"}
            or not address.is_absolute_url
            or address.username
            or address.password
            or address.path not in {"", "/"}
            or address.query
            or address.fragment
            or len(secret) < 32
            or re.fullmatch(_IMAGE, image) is None
            or (vm_image and re.fullmatch(_IMAGE, vm_image) is None)
            or settings.assistant_computer_default_provider
            not in {DOCKER_PROVIDER, *({VM_PROVIDER} if vm_image else set())}
        ):
            raise ValueError(
                "personal Computer requires deferred execution, a private node and a fixed image"
            )
        self.client = httpx.AsyncClient(
            base_url=address, trust_env=False, headers={"Authorization": "Bearer " + secret}
        )
        control = HttpComputerControl(self.client)
        self.drivers[DOCKER_PROVIDER] = DockerComputerDriver(control, image=image)
        if vm_image:
            self.drivers[VM_PROVIDER] = VmComputerDriver(
                control, image=vm_image,
                native_desktop=settings.assistant_computer_vm_native_desktop,
            )
        elif settings.assistant_computer_vm_native_desktop:
            raise ValueError("native VM desktop requires an enabled VM image")
        if settings.assistant_computer_remote_image:
            if re.fullmatch(_IMAGE, settings.assistant_computer_remote_image) is None:
                raise ValueError("remote computer requires pinned components")
            self.drivers[REMOTE_PROVIDER] = RemoteComputerDriver(
                control,
                image=settings.assistant_computer_remote_image,
                native_desktop=settings.assistant_computer_native_desktop,
                acp=settings.assistant_computer_acp_enabled,
            )
        self.default_provider = settings.assistant_computer_default_provider

    async def matches(self, session: Session) -> bool:
        return self.enabled and await self.access.is_personal_session(session)

    async def resolve_driver(self, tenant_id: str, user_id: str) -> DockerComputerDriver:
        """Route by the canonical computer row; a missing row uses the default.

        The registry row is the only provider truth. A configured driver that
        cannot serve a recorded provider fails the Run instead of silently
        serving a different machine.
        """

        if not self.drivers:
            raise RuntimeError("personal Computer execution is not configured on this process")
        try:
            computer: AssistantComputer | None = await self.registry.get(tenant_id, user_id)
        except NotFoundError:
            computer = None
        if computer is not None:
            driver = self.drivers.get(computer.provider)
            if driver is None:
                raise RuntimeError(
                    f"computer provider {computer.provider} is not enabled on this worker"
                )
            return driver
        return self.drivers[self.default_provider]

    async def provider_id(self, session: Session) -> str:
        return (await self.resolve_driver(session.tenant_id, session.user_id)).provider

    async def provider(
        self,
        session: Session,
        read_run: Callable[[str, str], Awaitable[Run]],
    ) -> ComputerSandboxProvider:
        driver = await self.resolve_driver(session.tenant_id, session.user_id)
        return ComputerSandboxProvider(
            self.registry,
            driver,
            session,
            run_reader=read_run,
            desktop_for_run=self._desktop_resolver(session, driver),
            desktops=self.desktops if self.per_bot_desktop else None,
            desktop=self.desktop,
            max_bytes=self.settings.workspace_archive_max_bytes,
            max_members=self.settings.workspace_archive_max_members,
            busy_wait_seconds=self.settings.assistant_computer_busy_wait_seconds,
        )

    def _desktop_resolver(
        self, session: Session, driver: DockerComputerDriver
    ) -> Callable[[Run], Awaitable[str | None]] | None:
        """Map an assistant Run to its Bot's desktop slot, when enabled.

        Legacy sessions (no specialist binding) keep the whole-machine lease,
        so rollout is switched per environment instead of per Bot.
        """

        if not self.per_bot_desktop or self.specialist_for_session is None:
            return None
        resolver = self.specialist_for_session
        image = driver.image

        async def desktop_for_run(run: Run) -> str | None:
            specialist_id = await resolver(session.tenant_id, session.user_id, run.session_id)
            if specialist_id is None:
                return None
            computer = await self.registry.get(session.tenant_id, session.user_id)
            desktop = await self.desktops.ensure(
                session.tenant_id,
                session.user_id,
                specialist_id,
                computer_id=computer.computer_id,
                image=image,
            )
            return desktop.desktop_id

        return desktop_for_run

    async def reclaim_desktops(self, *, idle_seconds: float) -> int:
        """Stop guest sessions for stopped or idle slots; never touches active work."""

        if not self.per_bot_desktop or self.client is None:
            return 0
        stopped = 0
        control = HttpComputerControl(self.client)
        for desktop in await self.desktops.reapable(idle_seconds=idle_seconds):
            if desktop.state is DesktopState.BUSY:
                continue
            payload = {
                "desktopId": desktop.desktop_id,
                "unixUser": desktop.unix_user,
                "display": desktop.display,
                "profile": desktop.profile_ref,
                "workspace": desktop.workspace_ref,
                "vncSocket": desktop.vnc_socket,
            }
            try:
                await control.desktop_slot(
                    desktop.tenant_id, desktop.user_id, "stop", payload
                )
            except (RuntimeError, NotFoundError):
                # A rebuilt guest simply has nothing to stop; the row is fenced
                # below either way, so one failure must not block the others.
                continue
            await self.desktops.stop(desktop.tenant_id, desktop.user_id, desktop.desktop_id)
            stopped += 1
        return stopped

    async def gui_overlay(
        self, session: Session, sandbox: object, handle: object
    ) -> PlatformToolOverlay:
        """Desktop tools only for a live Computer run with its real handle."""

        if not isinstance(sandbox, ComputerSandboxProvider):
            return PlatformToolOverlay()
        if not isinstance(handle, SandboxHandle) or not sandbox.owns(handle):
            return PlatformToolOverlay()
        gui = computer_gui_overlay(
            self.desktop,
            sandbox,
            handle,
            native_desktop=bool(getattr(sandbox.driver, "native_desktop", False)),
        )
        if not getattr(sandbox.driver, "acp", False):
            return gui
        acp = computer_acp_overlay(sandbox, handle)
        return PlatformToolOverlay(tools=(*gui.tools, *acp.tools),
                                   instructions=gui.instructions + "\n" + acp.instructions)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
