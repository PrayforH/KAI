"""Human-owned evolution workflow behind the existing Studio BFF."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException

from harness.api.dependencies import (
    ApiContainer,
    Identity,
    ensure_permission,
    get_container,
    require_identity,
)
from harness.evolution.models import (
    CreateEvolutionJob,
    EvolutionJob,
    ExperienceRequest,
    ProposeCandidate,
    ReviewRequest,
    RevisionRequest,
)

router = APIRouter(prefix="/v1/studio/evolution", tags=["evolution"])
User = Annotated[Identity, Depends(require_identity)]
Container = Annotated[ApiContainer, Depends(get_container)]


def human(identity: Identity) -> None:
    ensure_permission(identity, "studio:publish")
    if identity.authentication_method != "jwt":
        raise HTTPException(status_code=403, detail="A signed-in human session is required")


@router.get("", response_model=list[EvolutionJob])
async def list_jobs(identity: User, container: Container):
    ensure_permission(identity, "studio:read")
    return await container.evolution.list(identity.tenant_id, identity.user_id)


@router.post("", response_model=EvolutionJob, status_code=201)
async def create_job(body: CreateEvolutionJob, identity: User, container: Container):
    ensure_permission(identity, "studio:write")
    return await container.evolution.create(identity.tenant_id, identity.user_id, body)


@router.get("/{job_id}", response_model=EvolutionJob)
async def get_job(job_id: str, identity: User, container: Container):
    ensure_permission(identity, "studio:read")
    return await container.evolution.get(identity.tenant_id, identity.user_id, job_id)


@router.post("/{job_id}/candidates", response_model=EvolutionJob)
async def propose(job_id: str, body: ProposeCandidate, identity: User, container: Container):
    ensure_permission(identity, "studio:write")
    return await container.evolution.propose(identity.tenant_id, identity.user_id, job_id, body)


@router.post("/{job_id}/refresh", response_model=EvolutionJob)
async def refresh(job_id: str, body: RevisionRequest, identity: User, container: Container):
    ensure_permission(identity, "studio:write")
    return await container.evolution.refresh(
        identity.tenant_id, identity.user_id, job_id, body.expected_revision
    )


@router.post("/{job_id}/cancel", response_model=EvolutionJob)
async def cancel(job_id: str, body: RevisionRequest, identity: User, container: Container):
    ensure_permission(identity, "studio:write")
    return await container.evolution.cancel(
        identity.tenant_id, identity.user_id, job_id, body.expected_revision
    )


@router.post("/{job_id}/candidates/{candidate_id}/evaluate", response_model=EvolutionJob)
async def evaluate(
    job_id: str, candidate_id: str, body: RevisionRequest, identity: User, container: Container
):
    ensure_permission(identity, "studio:write")
    return await container.evolution.evaluate(
        identity.tenant_id, identity.user_id, job_id, candidate_id, body.expected_revision
    )


@router.post("/{job_id}/candidates/{candidate_id}/review", response_model=EvolutionJob)
async def review(
    job_id: str, candidate_id: str, body: ReviewRequest, identity: User, container: Container
):
    human(identity)
    return await container.evolution.review(
        identity.tenant_id, identity.user_id, job_id, candidate_id, body
    )


@router.post("/{job_id}/candidates/{candidate_id}/release", response_model=EvolutionJob)
async def release(
    job_id: str, candidate_id: str, body: RevisionRequest, identity: User, container: Container
):
    human(identity)
    return await container.evolution.release(
        identity.tenant_id, identity.user_id, job_id, candidate_id, body.expected_revision
    )


@router.post("/{job_id}/candidates/{candidate_id}/rollback", response_model=EvolutionJob)
async def rollback(
    job_id: str, candidate_id: str, body: RevisionRequest, identity: User, container: Container
):
    human(identity)
    return await container.evolution.rollback(
        identity.tenant_id, identity.user_id, job_id, candidate_id, body.expected_revision
    )


@router.post("/{job_id}/experiences", response_model=EvolutionJob)
async def experience(job_id: str, body: ExperienceRequest, identity: User, container: Container):
    ensure_permission(identity, "studio:write")
    return await container.evolution.add_experience(
        identity.tenant_id, identity.user_id, job_id, body
    )


@router.post("/{job_id}/experiences/{experience_id}/{action}", response_model=EvolutionJob)
async def experience_status(
    job_id: str,
    experience_id: str,
    action: Literal["reviewed", "deprecated"],
    body: RevisionRequest,
    identity: User,
    container: Container,
):
    human(identity)
    return await container.evolution.set_experience(
        identity.tenant_id, identity.user_id, job_id, experience_id, body.expected_revision, action
    )


@router.post("/{job_id}/candidates/{candidate_id}/observe", response_model=EvolutionJob)
async def observe(
    job_id: str, candidate_id: str, body: RevisionRequest, identity: User, container: Container
):
    ensure_permission(identity, "studio:write")
    return await container.evolution.observe(
        identity.tenant_id, identity.user_id, job_id, candidate_id, body.expected_revision
    )
