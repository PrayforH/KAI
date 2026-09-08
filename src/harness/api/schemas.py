"""Request schemas for the public Harness API."""

from typing import Any, Literal, cast

from pydantic import BaseModel, Field, model_validator

from harness.core.agent_dependencies import dependencies_from_snapshot
from harness.core.models import AgentVersion, ApprovalStatus


class PublishAgentRequest(BaseModel):
    path: str = Field(min_length=1)


class AgentSkillItem(BaseModel):
    name: str
    description: str


class AgentCatalogItem(BaseModel):
    name: str
    version: str
    display_name: str
    domain: str
    model_route: str | None = None
    model: str | None = None
    model_capabilities: tuple[str, ...] = ()
    skills: tuple[AgentSkillItem, ...] = ()
    mcp_references: tuple[str, ...] = ()
    knowledge_references: tuple[str, ...] = ()
    owner_user_id: str
    scope: Literal["personal", "team"] = "personal"
    space_id: str | None = None
    space_name: str | None = None
    runnable_by_viewer: bool = True
    # Stable Agent identity. Before migration this is absent and frontends must
    # fall back to the full coordinate scope:spaceId:ownerUserId:name@version.
    agent_id: str | None = None
    current_version: str | None = None
    connection_mode: Literal["caller_owned", "service_owned"] = "caller_owned"
    # Permission projection for the requesting user. can_view gates the space
    # page listing; can_chat gates the task selector; can_edit gates shared
    # draft editing.
    can_view: bool = True
    can_chat: bool = True
    can_edit: bool = False

    @classmethod
    def from_version(
        cls,
        version: AgentVersion,
        *,
        scope: Literal["personal", "team"] = "personal",
        space_id: str | None = None,
        space_name: str | None = None,
        runnable_by_viewer: bool = True,
        agent_id: str | None = None,
        current_version: str | None = None,
        connection_mode: Literal["caller_owned", "service_owned"] = "caller_owned",
        can_view: bool = True,
        can_chat: bool = True,
        can_edit: bool = False,
    ) -> "AgentCatalogItem":
        manifest = version.snapshot.get("manifest")
        metadata = (
            cast(dict[str, Any], manifest).get("metadata") if isinstance(manifest, dict) else None
        )
        labels = (
            cast(dict[str, Any], metadata).get("labels") if isinstance(metadata, dict) else None
        )
        label_values = cast(dict[str, Any], labels) if isinstance(labels, dict) else {}
        spec = cast(dict[str, Any], manifest).get("spec") if isinstance(manifest, dict) else None
        model_spec = cast(dict[str, Any], spec).get("model") if isinstance(spec, dict) else None
        model_values = cast(dict[str, Any], model_spec) if isinstance(model_spec, dict) else {}
        raw_capabilities = model_values.get("requiredCapabilities")
        capabilities = (
            tuple(str(value) for value in cast(list[object], raw_capabilities))
            if isinstance(raw_capabilities, list)
            else ()
        )
        raw_skills = version.snapshot.get("skill_snapshots", [])
        skills = (
            tuple(
                AgentSkillItem(
                    name=str(cast(dict[str, Any], item)["name"]),
                    description=str(cast(dict[str, Any], item).get("description", "")),
                )
                for item in cast(list[object], raw_skills)
                if isinstance(item, dict) and cast(dict[str, Any], item).get("name")
            )
            if isinstance(raw_skills, (list, tuple))
            else ()
        )
        dependencies = dependencies_from_snapshot(version.snapshot)
        return cls(
            name=version.name,
            version=version.version,
            display_name=str(label_values.get("display-name") or version.name),
            domain=str(label_values.get("domain") or "default"),
            model_route=(str(model_values["route"]) if model_values.get("route") else None),
            model=str(model_values["model"]) if model_values.get("model") else None,
            model_capabilities=capabilities,
            skills=skills,
            mcp_references=dependencies.mcp_references,
            knowledge_references=dependencies.knowledge_references,
            owner_user_id=version.owner_user_id,
            scope=scope,
            space_id=space_id,
            space_name=space_name,
            runnable_by_viewer=runnable_by_viewer,
            agent_id=agent_id,
            current_version=current_version,
            connection_mode=connection_mode,
            can_view=can_view,
            can_chat=can_chat,
            can_edit=can_edit,
        )


class CreateSessionRequest(BaseModel):
    agent_name: str = Field(min_length=1)
    agent_version: str | None = Field(default=None, min_length=1)
    environment: str | None = Field(default=None, pattern="^(test|canary|production)$")
    agent_owner_user_id: str | None = Field(default=None, min_length=1)
    space_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def select_version_or_environment(self) -> "CreateSessionRequest":
        if (self.agent_version is None) == (self.environment is None):
            raise ValueError("provide exactly one of agent_version or environment")
        return self


class CreateRunRequest(BaseModel):
    prompt: str = Field(min_length=1)
    input_artifact_ids: tuple[str, ...] = Field(default=())


class ApprovalDecisionRequest(BaseModel):
    decision: ApprovalStatus
