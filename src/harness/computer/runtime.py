"""Opt-in personal Computer routing and its private node transport."""

import re
from collections.abc import Awaitable, Callable

import httpx

from harness.computer.access import CanonicalComputerAccess
from harness.computer.control import HttpComputerControl
from harness.computer.docker import DockerComputerDriver
from harness.computer.registry import ComputerRegistry
from harness.computer.sandbox import ComputerSandboxProvider
from harness.config import Settings
from harness.core.models import Run, Session
from harness.storage.database import SessionFactory


class ComputerRuntime:
    def __init__(self, settings: Settings, sessions: SessionFactory, *, execution_enabled: bool):
        self.enabled = bool(settings.assistant_computer_owners)
        self.access = CanonicalComputerAccess(sessions, settings.assistant_computer_owners)
        self.registry = ComputerRegistry(sessions)
        self.settings = settings
        self.client: httpx.AsyncClient | None = None
        self.driver: DockerComputerDriver | None = None
        if not execution_enabled or not self.enabled:
            return
        address = httpx.URL(settings.assistant_computer_control_url)
        image = settings.assistant_computer_image
        secret = settings.assistant_computer_control_secret.get_secret_value()
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
            or re.fullmatch(r"(?:[A-Za-z0-9_./:-]+@)?sha256:[a-f0-9]{64}", image) is None
        ):
            raise ValueError(
                "personal Computer requires deferred execution, a private node and a fixed image"
            )
        self.client = httpx.AsyncClient(
            base_url=address, trust_env=False, headers={"Authorization": "Bearer " + secret}
        )
        self.driver = DockerComputerDriver(HttpComputerControl(self.client), image=image)

    async def matches(self, session: Session) -> bool:
        return self.enabled and await self.access.is_personal_session(session)

    def provider(
        self,
        session: Session,
        read_run: Callable[[str, str], Awaitable[Run]],
    ) -> ComputerSandboxProvider:
        if self.driver is None:
            raise RuntimeError("personal Computer execution is not configured on this process")
        return ComputerSandboxProvider(
            self.registry,
            self.driver,
            session,
            run_reader=read_run,
            max_bytes=self.settings.workspace_archive_max_bytes,
            max_members=self.settings.workspace_archive_max_members,
        )

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
