"""Runtime enforcement helpers for immutable Environment policy snapshots."""

from harness.core.errors import ConflictError
from harness.core.manifest import AgentManifestSnapshot
from harness.core.models import Session
from harness.deployments.models import EnvironmentPolicySnapshot, EnvironmentQuotaBoundary


def session_environment_policy(
    session: Session,
) -> EnvironmentPolicySnapshot | None:
    if session.environment_snapshot is None:
        return None
    try:
        return EnvironmentPolicySnapshot.model_validate(session.environment_snapshot)
    except ValueError as error:
        raise ConflictError("Session Environment policy snapshot is invalid") from error


def environment_quota_boundary(
    session: Session,
) -> EnvironmentQuotaBoundary | None:
    snapshot = session_environment_policy(session)
    return None if snapshot is None else snapshot.resource_policy.quota


def enforce_runtime_model_route(session: Session, route_id: str) -> None:
    """Apply the pinned Environment allow-list to task and admin overrides."""

    snapshot = session_environment_policy(session)
    if snapshot is None:
        return
    if route_id not in snapshot.resource_policy.allowed_model_routes:
        raise ConflictError(
            f"Session Environment snapshot denies model routes: {route_id}"
        )


def enforce_runtime_environment(
    session: Session,
    agent: AgentManifestSnapshot,
) -> None:
    snapshot = session_environment_policy(session)
    if snapshot is None:
        return
    policy = snapshot.resource_policy
    if (
        agent.tool_directory is not None
        and agent.tool_directory.catalog_revision != policy.capability_catalog_revision
    ):
        raise ConflictError("Session Environment snapshot denies the Agent tool directory revision")
    model_routes = {
        agent.manifest.spec.model.route,
        *(
            (agent.manifest.spec.model.fallback_route,)
            if agent.manifest.spec.model.fallback_route
            else ()
        ),
    }
    denied_routes = sorted(model_routes - set(policy.allowed_model_routes))
    if denied_routes:
        raise ConflictError(
            "Session Environment snapshot denies model routes: " + ", ".join(denied_routes)
        )
    mcp_references = {tool.mcp for tool in agent.manifest.spec.tools if tool.mcp is not None}
    denied_mcp = sorted(mcp_references - set(policy.allowed_mcp_references))
    if denied_mcp:
        raise ConflictError(
            "Session Environment snapshot denies MCP resources: " + ", ".join(denied_mcp)
        )
    denied_knowledge = sorted(
        set(agent.manifest.spec.knowledge_references) - set(policy.allowed_knowledge_references)
    )
    if denied_knowledge:
        raise ConflictError(
            "Session Environment snapshot denies Knowledge Bases: " + ", ".join(denied_knowledge)
        )
