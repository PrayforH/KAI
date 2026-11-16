"""群管理（改名/解散/删消息）与群资料库（文件、知识库绑定）的行为。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ag_ui.core import DocumentInputContent, TextInputContent

from harness.assistant.group_library import (
    group_document_download_url,
    group_document_name,
)
from harness.assistant.models import (
    AssistantGroup,
    AssistantGroupDocument,
    AssistantGroupMember,
    AssistantGroupMessage,
    AssistantGroupMessageAuthor,
    GroupMemberKind,
)
from harness.assistant.service import AssistantService
from harness.core.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
)

NOW = datetime(2026, 11, 13, tzinfo=UTC)


class FakeLibraryGraph:
    """只实现服务用到的仓储方法，保持与真实仓储同名同参。"""

    def __init__(self) -> None:
        self.groups: dict[str, AssistantGroup] = {}
        self.members: dict[str, list[AssistantGroupMember]] = {}
        self.messages: dict[str, list[AssistantGroupMessage]] = {}
        self.documents: dict[str, dict[str, SimpleNamespace]] = {}
        self.bindings: dict[str, list[str]] = {}
        self.client_ids: dict[tuple[str, str], str] = {}
        self.dissolved: set[str] = set()

    def seed_group(
        self, group_id: str = "group_a", *, user_id: str = "u", name: str = "项目群"
    ) -> AssistantGroup:
        group = AssistantGroup(
            groupId=group_id,
            name=name,
            creatorUserId=user_id,
            primaryPresent=True,
            createdAt=NOW,
            updatedAt=NOW,
        )
        self.groups[group_id] = group
        self.members[group_id] = [
            AssistantGroupMember(
                memberId="primary",
                kind=GroupMemberKind.PRIMARY,
                name="主助理",
                joinedAt=NOW,
            )
        ]
        self.messages[group_id] = []
        self.documents[group_id] = {}
        self.bindings[group_id] = []
        return group

    async def create_group(self, tenant_id, user_id, group, members):
        self.groups[group.group_id] = group
        self.members[group.group_id] = list(members)
        self.messages[group.group_id] = []
        self.documents[group.group_id] = {}
        self.bindings[group.group_id] = []
        return group.model_copy(update={"members": tuple(members)})

    async def get_group(self, tenant_id, user_id, group_id):
        group = self.groups.get(group_id)
        if group is None or group.creator_user_id != user_id:
            raise NotFoundError("Assistant group not found")
        return group.model_copy(update={"members": tuple(self.members[group_id])})

    async def group_owner(self, tenant_id, group_id):
        group = self.groups.get(group_id)
        return group.creator_user_id if group is not None else None

    async def update_group(self, tenant_id, user_id, group_id, *, fields):
        group = await self.get_group(tenant_id, user_id, group_id)
        updated = group.model_copy(update=dict(fields, updatedAt=NOW))
        self.groups[group_id] = updated
        return updated

    async def set_group_reply_as(self, tenant_id, user_id, group_id, reply_as):
        group = await self.get_group(tenant_id, user_id, group_id)
        updated = group.model_copy(update={"replyAs": reply_as})
        self.groups[group_id] = updated
        return updated

    async def delete_group(self, tenant_id, user_id, group_id, *, expected_name=None):
        group = await self.get_group(tenant_id, user_id, group_id)
        if expected_name is not None and group.name != expected_name:
            raise ConflictError(f"群名不一致，未解散；当前群名是『{group.name}』")
        self.dissolved.add(group_id)
        self.messages.pop(group_id, None)
        self.members.pop(group_id, None)
        self.documents.pop(group_id, None)
        self.bindings.pop(group_id, None)
        self.groups.pop(group_id, None)

    async def delete_group_message(
        self, tenant_id, user_id, group_id, message_id, *, actor_user_id
    ):
        group = await self.get_group(tenant_id, user_id, group_id)
        found = next(
            (item for item in self.messages[group_id] if item.message_id == message_id),
            None,
        )
        if found is None:
            raise NotFoundError("message not found")
        if found.status == "running":
            raise ConflictError("message is running")
        is_author = found.author is AssistantGroupMessageAuthor.USER and user_id == actor_user_id
        if not is_author and group.creator_user_id != actor_user_id:
            raise PermissionDeniedError("not allowed to delete this message")
        self.messages[group_id] = [
            item.model_copy(update={"reply_to_message_id": None})
            if item.reply_to_message_id == message_id
            else item
            for item in self.messages[group_id]
            if item.message_id != message_id
        ]

    async def add_group_message(
        self,
        tenant_id,
        user_id,
        message,
        *,
        thread_id=None,
        reply_to_message_id=None,
        client_message_id=None,
    ):
        stored = message.model_copy(
            update={"reply_to_message_id": reply_to_message_id}
        )
        self.messages[message.group_id].append(stored)
        if client_message_id:
            self.client_ids[(message.group_id, client_message_id)] = message.message_id
        return stored

    async def list_group_messages(self, tenant_id, user_id, group_id, limit=300):
        return list(self.messages[group_id])

    async def list_group_documents(self, tenant_id, user_id, group_id):
        await self.get_group(tenant_id, user_id, group_id)
        return [
            AssistantGroupDocument(
                documentId=row.document_id,
                groupId=group_id,
                name=row.name,
                contentType=row.content_type,
                sizeBytes=row.size_bytes,
                uploadedBy=row.user_id,
                uploadedByName="",
                createdAt=row.created_at,
                downloadUrl=group_document_download_url(group_id, row.document_id),
            )
            for row in self.documents[group_id].values()
        ]

    async def add_group_document(
        self, tenant_id, user_id, group_id, document, *, storage_key
    ):
        await self.get_group(tenant_id, user_id, group_id)
        self.documents[group_id][document.document_id] = SimpleNamespace(
            document_id=document.document_id,
            user_id=user_id,
            name=document.name,
            content_type=document.content_type,
            size_bytes=document.size_bytes,
            storage_key=storage_key,
            created_at=document.created_at,
        )
        return document

    async def get_group_document(self, tenant_id, user_id, group_id, document_id):
        await self.get_group(tenant_id, user_id, group_id)
        if document_id not in self.documents[group_id]:
            raise NotFoundError("document not found")
        return next(
            item
            for item in await self.list_group_documents(tenant_id, user_id, group_id)
            if item.document_id == document_id
        )

    async def group_document_row(self, tenant_id, user_id, group_id, document_id):
        await self.get_group(tenant_id, user_id, group_id)
        row = self.documents[group_id].get(document_id)
        if row is None:
            raise NotFoundError("document not found")
        return row

    async def group_document_rows(self, tenant_id, user_id, group_id, document_ids):
        await self.get_group(tenant_id, user_id, group_id)
        return {
            item: self.documents[group_id][item]
            for item in document_ids
            if item in self.documents[group_id]
        }

    async def delete_group_document(self, tenant_id, user_id, group_id, document_id):
        await self.get_group(tenant_id, user_id, group_id)
        row = self.documents[group_id].pop(document_id, None)
        if row is None:
            raise NotFoundError("document not found")
        return row

    async def list_group_knowledge_references(self, tenant_id, user_id, group_id):
        await self.get_group(tenant_id, user_id, group_id)
        return list(self.bindings[group_id])

    async def add_group_knowledge_reference(
        self, tenant_id, user_id, group_id, reference
    ):
        await self.get_group(tenant_id, user_id, group_id)
        if reference in self.bindings[group_id]:
            raise ConflictError("knowledge base already bound")
        self.bindings[group_id].append(reference)

    async def delete_group_knowledge_reference(
        self, tenant_id, user_id, group_id, reference
    ):
        await self.get_group(tenant_id, user_id, group_id)
        if reference not in self.bindings[group_id]:
            raise NotFoundError("knowledge base not bound")
        self.bindings[group_id].remove(reference)

    async def list_specialists(self, tenant_id, user_id, query=None):
        return []

    async def get_group_message_by_client_id(
        self, tenant_id, user_id, group_id, client_message_id
    ):
        message_id = self.client_ids.get((group_id, client_message_id))
        return next(
            (item for item in self.messages[group_id] if item.message_id == message_id),
            None,
        )

    async def get_group_message(self, tenant_id, user_id, group_id, message_id):
        for item in self.messages[group_id]:
            if item.message_id == message_id:
                return item
        raise NotFoundError("Group message not found")

    async def group_quoted_messages(self, tenant_id, user_id, group_id, message_ids):
        wanted = set(message_ids)
        return {
            item.message_id: item
            for item in self.messages.get(group_id, [])
            if item.message_id in wanted
        }

    async def set_group_message_run(
        self, tenant_id, user_id, group_id, message_id, *, run_id
    ):
        self.messages[group_id] = [
            item.model_copy(update={"run_id": run_id})
            if item.message_id == message_id
            else item
            for item in self.messages[group_id]
        ]


class FakeKnowledge:
    def __init__(self) -> None:
        self.bases = {
            "kb-handbook": SimpleNamespace(
                reference="kb-handbook",
                display_name="员工手册",
                kb_type="rag",
                engine="legacy",
                source_references=("src-a", "src-b"),
            )
        }

    async def get_base(self, tenant_id, reference, owner_user_id=None):
        base = self.bases.get(reference)
        if base is None:
            raise NotFoundError("missing")
        return base


class FakeArtifacts:
    def __init__(self) -> None:
        self.uploads: list[dict[str, object]] = []

    async def upload(self, *, tenant_id, user_id, name, media_type, content):
        self.uploads.append(
            {"name": name, "media_type": media_type, "content": content}
        )
        return SimpleNamespace(
            input_artifact_id=f"input_artifact_{len(self.uploads)}",
            size_bytes=len(content),
            name=name,
            media_type=media_type,
        )

    async def download(self, *, tenant_id, user_id, input_artifact_id):
        return (
            SimpleNamespace(name="方案.txt", media_type="text/plain"),
            b"hello",
        )


def _service(
    *,
    knowledge: FakeKnowledge | None = None,
    artifacts: FakeArtifacts | None = None,
) -> tuple[AssistantService, FakeLibraryGraph, AsyncMock]:
    repo = FakeLibraryGraph()
    agents, auth, projects, agui, runs, events = [AsyncMock() for _ in range(6)]
    auth.profile.side_effect = lambda tenant_id, user_id: (
        SimpleNamespace(display_name="小凯"),
        SimpleNamespace(role="owner"),
    )
    agui.get_binding.return_value = SimpleNamespace(
        agent_name="agent", agent_version="1.0", agent_owner_user_id="u", space_id=None
    )

    async def create_run_with_result(**kwargs):
        return SimpleNamespace(run=SimpleNamespace(run_id="run-1"))

    agui.create_run_with_result.side_effect = create_run_with_result
    service = AssistantService(
        repo,
        agents=agents,
        auth=auth,
        projects=projects,
        agui=agui,
        runs=runs,
        events=events,
        knowledge=knowledge,
        input_artifacts=artifacts,
    )
    return service, repo, agui


@pytest.mark.asyncio
async def test_group_can_be_renamed_and_described() -> None:
    service, repo, _ = _service()
    repo.seed_group()
    updated = await service.update_assistant_group(
        "t", "u", "group_a", changes={"name": "新名字", "description": "对外项目"}
    )
    assert updated.name == "新名字"
    assert updated.description == "对外项目"
    assert repo.groups["group_a"].name == "新名字"

    cleared = await service.update_assistant_group(
        "t", "u", "group_a", changes={"description": None}
    )
    assert cleared.description is None


@pytest.mark.asyncio
async def test_only_group_owner_may_edit_or_dissolve() -> None:
    service, repo, _ = _service()
    repo.seed_group(user_id="owner")
    with pytest.raises(PermissionDeniedError):
        await service.update_assistant_group(
            "t", "intruder", "group_a", changes={"name": "抢改"}
        )
    with pytest.raises(NotFoundError):
        await service.update_assistant_group(
            "t", "intruder", "group_missing", changes={"name": "抢改"}
        )


@pytest.mark.asyncio
async def test_dissolving_a_group_clears_every_group_table() -> None:
    service, repo, _ = _service()
    repo.seed_group()
    repo.documents["group_a"]["gdoc_1"] = SimpleNamespace(document_id="gdoc_1")
    repo.bindings["group_a"].append("kb-handbook")

    await service.delete_assistant_group("t", "u", "group_a")

    assert "group_a" in repo.dissolved
    assert "group_a" not in repo.groups
    assert "group_a" not in repo.messages
    assert "group_a" not in repo.members
    assert "group_a" not in repo.documents
    assert "group_a" not in repo.bindings


@pytest.mark.asyncio
async def test_message_delete_rules_and_quote_downgrade() -> None:
    service, repo, _ = _service()
    repo.seed_group()
    original = AssistantGroupMessage(
        messageId="gmsg_1",
        groupId="group_a",
        author=AssistantGroupMessageAuthor.USER,
        authorName="我",
        text="被引用的消息",
        status="completed",
        createdAt=NOW,
    )
    reply = AssistantGroupMessage(
        messageId="gmsg_2",
        groupId="group_a",
        author=AssistantGroupMessageAuthor.USER,
        authorName="我",
        text="引用一下",
        replyToMessageId="gmsg_1",
        status="completed",
        createdAt=NOW,
    )
    running = AssistantGroupMessage(
        messageId="gmsg_3",
        groupId="group_a",
        author=AssistantGroupMessageAuthor.PRIMARY,
        authorName="主助理",
        text="",
        status="running",
        createdAt=NOW,
    )
    repo.messages["group_a"] = [original, reply, running]

    with pytest.raises(ConflictError):
        await service.delete_assistant_group_message("t", "u", "group_a", "gmsg_3")
    with pytest.raises(NotFoundError):
        await service.delete_assistant_group_message("t", "u", "group_a", "gmsg_missing")

    await service.delete_assistant_group_message("t", "u", "group_a", "gmsg_1")

    remaining = {item.message_id: item for item in repo.messages["group_a"]}
    assert "gmsg_1" not in remaining
    # 引用方保留正文，但引用指针被清掉，读接口因此省略 replyTo。
    assert remaining["gmsg_2"].reply_to_message_id is None


@pytest.mark.asyncio
async def test_unowned_messages_still_deletable_by_creator() -> None:
    service, repo, _ = _service()
    repo.seed_group()
    repo.messages["group_a"] = [
        AssistantGroupMessage(
            messageId="gmsg_bot",
            groupId="group_a",
            author=AssistantGroupMessageAuthor.PRIMARY,
            authorName="主助理",
            text="机器人回复",
            status="completed",
            createdAt=NOW,
        )
    ]
    await service.delete_assistant_group_message("t", "u", "group_a", "gmsg_bot")
    assert repo.messages["group_a"] == []


@pytest.mark.asyncio
async def test_library_upload_list_and_delete() -> None:
    artifacts = FakeArtifacts()
    service, repo, _ = _service(artifacts=artifacts)
    repo.seed_group()

    document = await service.add_assistant_group_document(
        "t", "u", "group_a", name="方案.txt", media_type="text/plain", content=b"abc"
    )
    assert document.name == "方案.txt"
    assert document.uploaded_by_name == "小凯"
    assert document.download_url.endswith(f"/documents/{document.document_id}/content")

    library = await service.get_assistant_group_library("t", "u", "group_a")
    assert [item.document_id for item in library.documents] == [document.document_id]

    await service.delete_assistant_group_document(
        "t", "u", "group_a", document.document_id
    )
    library = await service.get_assistant_group_library("t", "u", "group_a")
    assert library.documents == ()


@pytest.mark.asyncio
async def test_knowledge_base_binding_is_unique_and_summarised() -> None:
    service, repo, _ = _service(knowledge=FakeKnowledge())
    repo.seed_group()

    summary = await service.bind_assistant_group_knowledge_base(
        "t", "u", "group_a", "kb-handbook"
    )
    assert summary.name == "员工手册"
    assert summary.source_count == 2

    with pytest.raises(ConflictError):
        await service.bind_assistant_group_knowledge_base(
            "t", "u", "group_a", "kb-handbook"
        )
    with pytest.raises(NotFoundError):
        await service.bind_assistant_group_knowledge_base(
            "t", "u", "group_a", "kb-missing"
        )

    library = await service.get_assistant_group_library("t", "u", "group_a")
    assert [item.reference for item in library.knowledge_bases] == ["kb-handbook"]

    await service.unbind_assistant_group_knowledge_base("t", "u", "group_a", "kb-handbook")
    assert repo.bindings["group_a"] == []


@pytest.mark.asyncio
async def test_message_attachments_must_come_from_the_group_library() -> None:
    artifacts = FakeArtifacts()
    service, repo, agui = _service(artifacts=artifacts)
    group = repo.seed_group()
    document = await service.add_assistant_group_document(
        "t", "u", "group_a", name="方案.txt", media_type="text/plain", content=b"abc"
    )
    repo.seed_group("group_b")
    other = await service.add_assistant_group_document(
        "t", "u", "group_b", name="别的.txt", media_type="text/plain", content=b"x"
    )

    with pytest.raises(InvalidRequestError):
        await service.post_assistant_group_message(
            "t", "u", "group_a", text="带附件", attachment_ids=[other.document_id]
        )

    result = await service.post_assistant_group_message(
        "t", "u", "group_a", text="带附件", attachment_ids=[document.document_id]
    )
    assert [item.document_id for item in result.message.attachments] == [
        document.document_id
    ]
    request = agui.create_run_with_result.await_args.kwargs["request"]
    parts = request.messages[0].content
    assert isinstance(parts, list)
    assert any(isinstance(item, TextInputContent) for item in parts)
    assert any(isinstance(item, DocumentInputContent) for item in parts)
    assert group.group_id == "group_a"


@pytest.mark.asyncio
async def test_group_knowledge_bases_are_injected_into_bot_runs() -> None:
    service, repo, agui = _service(knowledge=FakeKnowledge())
    repo.seed_group()
    await service.bind_assistant_group_knowledge_base(
        "t", "u", "group_a", "kb-handbook"
    )

    await service.post_assistant_group_message("t", "u", "group_a", text="查一下")

    request = agui.create_run_with_result.await_args.kwargs["request"]
    assert request.forwarded_props["knowledgeReferences"] == ["kb-handbook"]


@pytest.mark.asyncio
async def test_deleted_knowledge_base_is_skipped_without_failing_the_reply() -> None:
    knowledge = FakeKnowledge()
    service, repo, agui = _service(knowledge=knowledge)
    repo.seed_group()
    repo.bindings["group_a"].append("kb-deleted")

    await service.post_assistant_group_message("t", "u", "group_a", text="还在吗")

    request = agui.create_run_with_result.await_args.kwargs["request"]
    assert "knowledgeReferences" not in request.forwarded_props


# --- 全角标点保留：改名/描述/文件名不做 NFKC -------------------------------


@pytest.mark.asyncio
async def test_renaming_keeps_full_width_punctuation() -> None:
    service, repo, _ = _service()
    repo.seed_group()
    name = "项目：一期（终稿）"
    description = "验收口径：一期「已定」，二期再看。"

    updated = await service.update_assistant_group(
        "t", "u", "group_a", changes={"name": name, "description": description}
    )
    assert updated.name == name
    assert updated.description == description
    assert repo.groups["group_a"].name == name
    assert repo.groups["group_a"].description == description

    reread = await service.get_assistant_group("t", "u", "group_a")
    assert reread.name == name
    assert reread.description == description


@pytest.mark.asyncio
async def test_group_description_limits_are_unchanged() -> None:
    service, repo, _ = _service()
    repo.seed_group()
    with pytest.raises(ConflictError):
        await service.update_assistant_group(
            "t", "u", "group_a", changes={"description": "述" * 501}
        )
    with pytest.raises(ConflictError):
        await service.update_assistant_group(
            "t", "u", "group_a", changes={"description": "描述\x07内容"}
        )


def test_group_document_name_keeps_full_width_punctuation() -> None:
    name = "报告（终稿）：第一版.pdf"
    assert group_document_name(name) == name
    assert group_document_name("  方案  说明.txt  ") == "方案 说明.txt"
    with pytest.raises(ConflictError):
        group_document_name("坏\x07名字.pdf")
    with pytest.raises(ConflictError):
        group_document_name("   ")
    assert len(group_document_name("文" * 300)) == 255
