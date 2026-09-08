"""Authenticated tenant-scoped user group API for batch Agent ACL grants."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from harness.api.dependencies import ApiContainer, Identity, get_container, require_identity
from harness.sharing.models import GroupMember, UserGroup

router = APIRouter(prefix="/groups", tags=["user groups"])


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)


class GroupMemberRequest(BaseModel):
    user_id: str = Field(min_length=1)


class GroupDetail(BaseModel):
    group: UserGroup
    members: list[GroupMember]


@router.get("", response_model=list[UserGroup])
async def list_groups(
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> list[UserGroup]:
    return await container.team_spaces.list_groups(identity.tenant_id)


@router.post(
    "", response_model=UserGroup, status_code=status.HTTP_201_CREATED
)
async def create_group(
    body: CreateGroupRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> UserGroup:
    return await container.team_spaces.create_group(
        identity.tenant_id, identity.user_id, body.name, body.description
    )


@router.get("/{group_id}", response_model=GroupDetail)
async def get_group(
    group_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> GroupDetail:
    group, members = await container.team_spaces.get_group(
        identity.tenant_id, group_id
    )
    return GroupDetail(group=group, members=members)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> Response:
    await container.team_spaces.delete_group(identity.tenant_id, group_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{group_id}/members",
    response_model=GroupMember,
    status_code=status.HTTP_201_CREATED,
)
async def add_group_member(
    group_id: str,
    body: GroupMemberRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> GroupMember:
    return await container.team_spaces.add_group_member(
        identity.tenant_id, group_id, body.user_id
    )


@router.delete(
    "/{group_id}/members/{target_user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_group_member(
    group_id: str,
    target_user_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> Response:
    await container.team_spaces.remove_group_member(
        identity.tenant_id, group_id, target_user_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
