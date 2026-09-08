"""Session lifecycle use cases."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Literal

from harness.application.agent_assets import resolve_published_agent_versions
from harness.application.types import Clock, IdGenerator
from harness.core.errors import ConflictError, NotFoundError
from harness.core.manifest import AgentManifestSnapshot
from harness.core.models import AgentVersionStatus, Session
from harness.core.ports import AgentRegistry, SessionRepository
from harness.deployments.models import DeploymentResolution, EnvironmentName
from harness.knowledge.models import KnowledgeSnapshotBinding

DeploymentResolver = Callable[
    [str, str, str, EnvironmentName, str], Awaitable[DeploymentResolution]
]
KnowledgeBindingResolver = Callable[
    [str, str, Sequence[str], tuple[str, ...]],
    Awaitable[Sequence[KnowledgeSnapshotBinding]],
]


class SessionService:
    def __init__(
        self,
        registry: AgentRegistry,
        sessions: SessionRepository,
        *,
        clock: Clock,
        id_generator: IdGenerator,
        require_published_dependencies: bool = False,
        deployment_resolver: DeploymentResolver | None = None,
        knowledge_binding_resolver: KnowledgeBindingResolver | None = None,
    ) -> None:
        self._registry = registry
        self._sessions = sessions
        self._clock = clock
        self._id_generator = id_generator
        self._require_published_dependencies = require_published_dependencies
        self._deployment_resolver = deployment_resolver
        self._knowledge_binding_resolver = knowledge_binding_resolver

    def configure_deployment_resolver(self, resolver: DeploymentResolver) -> None:
        if self._deployment_resolver is not None:
            raise RuntimeError("deployment resolver is already configured")
        self._deployment_resolver = resolver

    async def create(
        self,
        tenant_id: str,
        user_id: str,
        agent_name: str,
        agent_version: str | None,
        *,
        session_id: str | None = None,
        environment: EnvironmentName | None = None,
        team_ids: tuple[str, ...] = (),
        api_key_id: str | None = None,
        agent_owner_user_id: str | None = None,
        connection_mode: Literal["caller_owned", "service_owned"] = "caller_owned",
        preview: bool = False,
    ) -> Session:
        if preview and (agent_version is None or environment is not None):
            raise ConflictError("preview sessions require an explicit Agent version")
        if (agent_version is None) == (environment is None):
            raise ConflictError("provide exactly one of agent_version or environment")
        resolved_session_id = session_id or self._id_generator("session")
        resolved_agent_owner = agent_owner_user_id or user_id
        deployment_snapshot_id: str | None = None
        environment_snapshot: dict[str, object] | None = None
        if environment is not None:
            if self._deployment_resolver is None:
                raise ConflictError("environment deployment resolution is unavailable")
            resolution = await self._deployment_resolver(
                tenant_id,
                resolved_agent_owner,
                agent_name,
                environment,
                resolved_session_id,
            )
            agent_version = resolution.agent_version
            deployment_snapshot_id = resolution.snapshot_id
            environment_snapshot = resolution.environment_policy_snapshot.model_dump(
                mode="json",
                by_alias=True,
            )
            required_credential_scope = "workload" if user_id.startswith("trigger:") else "user"
            allowed_credential_scopes = {
                item.value
                for item in resolution.environment_policy_snapshot.resource_policy.credential_scopes
            }
            if required_credential_scope not in allowed_credential_scopes:
                raise ConflictError(
                    f"Environment does not allow {required_credential_scope} credentials"
                )
        assert agent_version is not None
        version = await self._registry.get(
            tenant_id, resolved_agent_owner, agent_name, agent_version
        )
        allowed_status = (
            AgentVersionStatus.VALIDATED if preview else AgentVersionStatus.PUBLISHED
        )
        if version.status is not allowed_status:
            raise ConflictError("sessions can only use a published Agent version")
        if self._require_published_dependencies:
            await resolve_published_agent_versions(
                self._registry,
                tenant_id=tenant_id,
                owner_user_id=resolved_agent_owner,
                agent_name=agent_name,
                agent_version=agent_version,
                allow_validated_graph=preview,
            )
        snapshot = AgentManifestSnapshot.model_validate(version.snapshot)
        knowledge_bindings: tuple[dict[str, object], ...] = ()
        if snapshot.manifest.spec.knowledge_references:
            if self._knowledge_binding_resolver is None:
                raise ConflictError("knowledge snapshot resolution is unavailable")
            resolved = await self._knowledge_binding_resolver(
                tenant_id,
                user_id,
                snapshot.manifest.spec.knowledge_references,
                team_ids,
            )
            knowledge_bindings = tuple(
                item.model_dump(mode="json", by_alias=True) for item in resolved
            )
        session = Session(
            session_id=resolved_session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_owner_user_id=resolved_agent_owner,
            team_ids=team_ids,
            api_key_id=api_key_id,
            runtime_type=snapshot.manifest.spec.runtime,
            agent_name=agent_name,
            agent_version=agent_version,
            created_at=self._clock(),
            environment=(
                "preview" if preview else environment.value if environment is not None else None
            ),
            deployment_snapshot_id=deployment_snapshot_id,
            environment_snapshot=environment_snapshot,
            knowledge_snapshot_bindings=knowledge_bindings,
            connection_mode=connection_mode,
        )
        expected_environment = (
            "preview" if preview else environment.value if environment is not None else None
        )
        try:
            await self._sessions.add(session)
        except ConflictError as error:
            if session_id is None:
                raise
            existing = await self._sessions.get(tenant_id, session_id)
            if (
                existing.user_id != user_id
                or existing.resolved_agent_owner_user_id != resolved_agent_owner
                or existing.team_ids != team_ids
                or existing.api_key_id != api_key_id
                or existing.runtime_type != snapshot.manifest.spec.runtime
                or existing.agent_name != agent_name
                or existing.agent_version != agent_version
                or existing.environment != expected_environment
                or existing.deployment_snapshot_id != deployment_snapshot_id
                or existing.environment_snapshot != environment_snapshot
                or existing.knowledge_snapshot_bindings != knowledge_bindings
            ):
                raise ConflictError(
                    "deterministic Session ID was reused for another Eval Case"
                ) from error
            return existing
        return session

    async def get(self, tenant_id: str, session_id: str) -> Session:
        return await self._sessions.get(tenant_id, session_id)

    async def list_for_ids(
        self, tenant_id: str, session_ids: list[str]
    ) -> list[Session]:
        return await self._sessions.list_for_ids(tenant_id, session_ids)

    async def clone_for_context_rebase(
        self,
        tenant_id: str,
        user_id: str,
        source_session_id: str,
        *,
        session_id: str,
    ) -> Session:
        """Create a fresh SDK identity while pinning the exact Harness snapshot."""

        source = await self._sessions.get(tenant_id, source_session_id)
        if source.user_id != user_id:
            raise NotFoundError(f"session not found: {source_session_id}")
        cloned = source.model_copy(
            update={
                "session_id": session_id,
                "runtime_thread_id": None,
                "claude_session_id": None,
                "created_at": self._clock(),
            }
        )
        try:
            await self._sessions.add(cloned)
            return cloned
        except ConflictError as error:
            existing = await self._sessions.get(tenant_id, session_id)
            expected = cloned.model_dump(exclude={"created_at"})
            actual = existing.model_dump(exclude={"created_at"})
            if actual != expected:
                raise ConflictError(
                    "context rebase Session ID was reused for another snapshot"
                ) from error
            return existing
