"""Agent validation and publication use cases."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from harness.agent_package import (
    AgentBundleValidationError,
    AgentPackageReport,
    check_agent_package,
    extract_agent_bundle,
)
from harness.application.types import Clock
from harness.core.errors import ConflictError
from harness.core.manifest import AgentManifestSnapshot, load_manifest
from harness.core.models import AgentVersion, AgentVersionStatus
from harness.core.ports import AgentIdentityProvider, AgentRegistry


class AgentService:
    def __init__(
        self,
        registry: AgentRegistry,
        *,
        clock: Clock,
        environment: Literal["local", "test", "production"] = "local",
        allow_path_publication: bool | None = None,
        default_manifest_path: str | Path | None = None,
        agent_ids: AgentIdentityProvider | None = None,
    ) -> None:
        self._registry = registry
        self._clock = clock
        self._environment: Literal["local", "test", "production"] = environment
        self.path_publication_enabled = (
            environment != "production"
            if allow_path_publication is None
            else allow_path_publication
        )
        self._default_manifest_path = (
            Path(default_manifest_path) if default_manifest_path is not None else None
        )
        self._default_report: AgentPackageReport | None = None
        self._agent_ids = agent_ids

    def validate(
        self,
        path: str | Path,
        *,
        environment: Literal["local", "test", "production"] | None = None,
    ) -> AgentManifestSnapshot:
        active_environment: Literal["local", "test", "production"] = (
            environment or self._environment
        )
        if active_environment == "production":
            return check_agent_package(path, environment=active_environment).snapshot
        return load_manifest(path, environment=active_environment)

    async def list_published(self, tenant_id: str, owner_user_id: str) -> list[AgentVersion]:
        return [
            version
            for version in await self._registry.list_for_user(tenant_id, owner_user_id)
            if version.status is AgentVersionStatus.PUBLISHED
        ]

    async def get_published(
        self,
        tenant_id: str,
        owner_user_id: str,
        name: str,
        version: str,
    ) -> AgentVersion:
        value = await self._registry.get(tenant_id, owner_user_id, name, version)
        if value.status is not AgentVersionStatus.PUBLISHED:
            raise ConflictError("only published Agent versions can be shared")
        return value

    async def list_published_catalog(
        self, tenant_id: str, owner_user_id: str
    ) -> list[AgentVersion]:
        """Return lightweight published versions and provision the default once.

        Unlike the legacy route sequence, this performs a single version-list
        query and never loads packaged files into the navigation request.
        """
        versions = await self._registry.list_catalog_for_user(tenant_id, owner_user_id)
        default = await self._ensure_user_default_from(
            tenant_id,
            owner_user_id,
            versions,
        )
        if default is not None and not any(
            item.name == default.name and item.version == default.version
            for item in versions
        ):
            versions.append(default)
            versions.sort(key=lambda item: (item.name, item.version))
        return [
            version
            for version in versions
            if version.status is AgentVersionStatus.PUBLISHED
        ]

    async def ensure_user_default(
        self, tenant_id: str, owner_user_id: str
    ) -> AgentVersion | None:
        """Idempotently provision the platform default into a user's private catalog."""

        existing_versions = await self._registry.list_catalog_for_user(
            tenant_id, owner_user_id
        )
        default = await self._ensure_user_default_from(
            tenant_id,
            owner_user_id,
            existing_versions,
        )
        if default is None:
            return None
        if any(
            item.name == default.name and item.version == default.version
            for item in existing_versions
        ):
            # Preserve the public method's full-version contract. The catalog
            # path calls the private helper directly and avoids this payload.
            return await self._registry.get(
                tenant_id,
                owner_user_id,
                default.name,
                default.version,
            )
        return default

    async def _ensure_user_default_from(
        self,
        tenant_id: str,
        owner_user_id: str,
        existing_versions: list[AgentVersion],
    ) -> AgentVersion | None:
        """Provision the default using an already-loaded catalog projection."""

        if self._default_manifest_path is None:
            return None
        if self._default_report is None:
            self._default_report = check_agent_package(
                self._default_manifest_path, environment="production"
            )
        report = self._default_report
        snapshot = report.snapshot
        name = snapshot.manifest.metadata.name
        version = snapshot.manifest.metadata.version
        for existing in existing_versions:
            if (
                existing.name == name
                and existing.version == version
                and existing.status is AgentVersionStatus.PUBLISHED
            ):
                return existing
        return await self._publish_snapshot(
            tenant_id,
            owner_user_id,
            snapshot,
            package_hash=report.package_hash,
        )

    async def publish(
        self,
        tenant_id: str,
        owner_user_id: str,
        path: str | Path,
        *,
        environment: Literal["local", "test", "production"] | None = None,
    ) -> AgentVersion:
        snapshot = self.validate(path, environment=environment)
        return await self._publish_snapshot(tenant_id, owner_user_id, snapshot)

    async def publish_bundle(
        self,
        tenant_id: str,
        owner_user_id: str,
        content: bytes,
        *,
        agent_id: str | None = None,
    ) -> AgentVersion:
        with TemporaryDirectory(prefix="harness-agent-bundle-") as directory:
            manifest, claimed_hash, claimed_package_hash = extract_agent_bundle(
                content, destination=directory
            )
            report = check_agent_package(manifest, environment="production")
            snapshot = report.snapshot
            if snapshot.content_hash != claimed_hash:
                raise AgentBundleValidationError(
                    "Agent bundle provenance does not match its immutable snapshot"
                )
            if report.package_hash != claimed_package_hash:
                raise AgentBundleValidationError(
                    "Agent bundle provenance does not match its release package"
                )
            return await self._publish_snapshot(
                tenant_id,
                owner_user_id,
                snapshot,
                package_hash=report.package_hash,
                agent_id=agent_id,
            )

    async def register_preview_snapshot(
        self,
        tenant_id: str,
        owner_user_id: str,
        snapshot: AgentManifestSnapshot,
        *,
        version: str,
        package_hash: str | None = None,
        agent_id: str | None = None,
    ) -> AgentVersion:
        """Register an immutable Studio-only snapshot without publishing it."""

        manifest = snapshot.manifest
        candidate = AgentVersion(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            name=manifest.metadata.name,
            version=version,
            status=AgentVersionStatus.VALIDATED,
            manifest_hash=snapshot.content_hash,
            package_hash=package_hash,
            snapshot=snapshot.model_dump(mode="json"),
            created_at=self._clock(),
            agent_id=agent_id,
        )
        try:
            await self._registry.add(candidate)
        except ConflictError:
            existing = await self._registry.get(
                tenant_id, owner_user_id, candidate.name, candidate.version
            )
            if (
                existing.status is not AgentVersionStatus.VALIDATED
                or existing.manifest_hash != candidate.manifest_hash
                or existing.package_hash != candidate.package_hash
            ):
                raise
            return existing
        return candidate

    async def _publish_snapshot(
        self,
        tenant_id: str,
        owner_user_id: str,
        snapshot: AgentManifestSnapshot,
        *,
        package_hash: str | None = None,
        agent_id: str | None = None,
    ) -> AgentVersion:
        manifest = snapshot.manifest
        resolved_agent_id = agent_id
        if resolved_agent_id is None and self._agent_ids is not None:
            resolved_agent_id = await self._agent_ids.get_or_create_personal_agent_id(
                tenant_id, owner_user_id, manifest.metadata.name
            )
        version = AgentVersion(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            name=manifest.metadata.name,
            version=manifest.metadata.version,
            status=AgentVersionStatus.PUBLISHED,
            manifest_hash=snapshot.content_hash,
            package_hash=package_hash,
            snapshot=snapshot.model_dump(mode="json"),
            created_at=self._clock(),
            agent_id=resolved_agent_id,
        )
        try:
            await self._registry.add(version)
        except ConflictError:
            existing = await self._registry.get(
                tenant_id,
                owner_user_id,
                manifest.metadata.name,
                manifest.metadata.version,
            )
            if existing.manifest_hash != version.manifest_hash or (
                package_hash is not None and existing.package_hash != package_hash
            ):
                raise
            if self._agent_ids is not None and existing.agent_id is not None:
                # A repeated immutable publish is also the recovery path when
                # the registry write succeeded but the current pointer update
                # was interrupted.
                await self._agent_ids.promote_personal_agent_version(
                    tenant_id,
                    owner_user_id,
                    existing.agent_id,
                    existing.name,
                    existing.version,
                )
            return existing
        if self._agent_ids is not None and version.agent_id is not None:
            await self._agent_ids.promote_personal_agent_version(
                tenant_id,
                owner_user_id,
                version.agent_id,
                version.name,
                version.version,
            )
        return version
