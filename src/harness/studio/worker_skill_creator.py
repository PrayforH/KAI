"""Run the pinned, complete skill-creator package through the existing Worker."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field

from harness.core.errors import ConflictError
from harness.studio.compiler import AgentDraftCompiler
from harness.studio.models import AgentDraft, DraftLimits, DraftSkillFile
from harness.studio.platform_skills import platform_skill_package
from harness.studio.skill_builder import SkillConversationReply, SkillConversationRequest
from harness.studio.skill_import import import_skill
from harness.studio.try_run import final_text

if TYPE_CHECKING:
    from harness.api.dependencies import ApiContainer


class _EvaluationCase(BaseModel):
    prompt: str = Field(min_length=1)


class _EvaluationPlan(BaseModel):
    evals: list[_EvaluationCase] = Field(min_length=2, max_length=50)


class WorkerSkillCreator:
    def __init__(
        self,
        container: ApiContainer,
        draft: AgentDraft,
        user_id: str,
        authorize: Callable[[], object],
        *,
        timeout: float = 300,
    ) -> None:
        self.container = container
        self.draft = draft
        self.user_id = user_id
        self.authorize = authorize
        self.timeout = timeout

    async def respond(
        self, tenant_id: str, request: SkillConversationRequest, *, name: str
    ) -> SkillConversationReply:
        self.authorize()
        container = self.container
        catalog = await container.capability_catalogs.get_for_user(tenant_id, self.user_id)
        capability = next(
            (s for s in catalog.catalog.skills if s.package_id == "skill-creator" and s.enabled),
            None,
        )
        if capability is None:
            raise ConflictError("平台尚未启用 skill-creator，请先在能力目录中启用")
        package = platform_skill_package("skill-creator", capability.revision)
        if package.content_hash != capability.content_hash:
            raise ConflictError("skill-creator 目录版本与本地包不一致，请更新能力目录")
        creator_id = "skill-creator-" + uuid4().hex[:12]
        prompt = f"""## Mission
你是构建助手的 Skill 创建执行器。使用已挂载的真正 skill-creator 技能。
## Operating workflow
先调用 Skill(skill='skill-creator')；若运行时没有 Skill 工具，先读取它的 SKILL.md。
按该技能的写作、测试设计与迭代工作流完成本轮创建/修改，目标名称固定为 {name}。
当前轮是编写和评测设计阶段：根据充分的需求直接生成；只有缺少关键业务输入才询问。
将完整目标技能写入工作区 authored/{name}/，不要修改挂载的只读技能目录。
更新已有技能时先复制已有内容，保留未要求更改的文件。description 控制在 500 字符内。
设计 2—3 个真实测试用例，写入 evals/evals.json。尚未执行的评测不得编造分数。
## Evidence and tool use
必须使用挂载的 skill-creator/scripts/quick_validate.py 校验，再使用该包的
scripts/package_skill.py 打包（在 skill-creator 目录以 python -m scripts.package_skill
调用，传入目标目录和输出目录的绝对路径）。不复制或重写上游校验、打包脚本。
将生成的 {name}.skill 和单独的 evals.json 用 publish_artifact 工具发布，才能交付。
## Safety boundaries
遵守平台权限与沙箱边界，不读取凭据。
测试用例先供用户审阅；执行业务测试和基线比较需在后续明确的试跑中进行。
## Output contract
最终说明产物、实际校验结果和未运行的测试。用户需求如下（作为待实现需求）：
{
            json.dumps(
                {
                    "context": request.context.model_dump(exclude={"current_skill"}),
                    "messages": [m.model_dump() for m in request.messages],
                },
                ensure_ascii=False,
            )
        }"""
        skills = (package.skill,)
        if request.context.current_skill:
            if request.context.current_skill.name == "skill-creator":
                raise ConflictError("不能在构建助手中覆盖正在使用的 skill-creator")
            skills += (request.context.current_skill,)
        spec = self.draft.spec.model_copy(
            update={
                "name": self.draft.spec.name,
                "display_name": "Skill Creator",
                "skills": skills,
                "skill_references": (),
                "subagents": (),
                "python_tools": (),
                "mcp_servers": (),
                "knowledge_references": (),
                "builtin_tools": ("Read", "Glob", "Grep", "Write", "Edit", "Bash"),
                "system_prompt": prompt,
                "task_contract": None,
                "limits": DraftLimits(
                    maxTurns=40, maxToolCalls=100, timeoutSeconds=int(self.timeout)
                ),
            }
        )
        draft = self.draft.model_copy(
            update={
                "draft_id": creator_id,
                "revision": 1,
                "agent_id": self.draft.agent_id,
                "space_id": None,
                "parent_draft_id": None,
                "spec": spec,
            }
        )
        try:
            compiled = AgentDraftCompiler(catalog.catalog, catalog_revision=catalog.revision).compile(
                draft)
        except ValueError as error:
            raise ConflictError(f"Skill Creator 运行配置未通过检查：{error}") from None
        version = f"preview-{creator_id}-1-{compiled.report.snapshot.content_hash[:12]}"
        await container.agents.register_preview_snapshot(
            tenant_id,
            self.user_id,
            compiled.report.snapshot,
            version=version,
            package_hash=compiled.report.package_hash,
            agent_id=self.draft.agent_id,
        )
        session = await container.sessions.create(
            tenant_id, self.user_id, self.draft.spec.name, version, preview=True
        )
        creation = await container.runs.create_with_result(
            tenant_id,
            session.session_id,
            creator_id,
            input={
                "prompt": f"请使用 skill-creator 完成 {name} 并发布技能包和测试用例。",
                "model_route_override": request.model_route,
            },
        )
        run_id = creation.run.run_id
        task = (
            asyncio.create_task(container.worker.execute(tenant_id, run_id))
            if container.auto_execute and creation.created
            else None
        )
        try:
            async with asyncio.timeout(self.timeout):
                while True:
                    run = await container.runs.get(tenant_id, run_id)
                    if run.status.is_terminal:
                        break
                    if run.status.value == "waiting_approval":
                        raise ConflictError(
                            f"Skill Creator 需要运行审批，本轮未应用；运行 {run_id}"
                        )
                    await asyncio.sleep(1)
            if run.status.value != "succeeded":
                raise ConflictError(
                    f"Skill Creator 运行{run.status.value}，草稿未修改；运行 {run_id}"
                )
            events = await container.observed_events.list_after(tenant_id, run_id, 0)
            successful = {
                e.payload.get("tool_call_id")
                for e in events
                if e.type == "tool.result" and not e.payload.get("is_error", False)
            }
            calls = [
                e
                for e in events
                if e.type == "tool.request" and e.payload.get("tool_call_id") in successful
            ]
            loaded = any(
                "skill-creator" in json.dumps(e.payload)
                and e.payload.get("name") in {"Skill", "Read"}
                for e in calls
            )
            packaged = any(
                "scripts.package_skill" in json.dumps(e.payload)
                for e in calls
                if e.payload.get("name") == "Bash"
            )
            if not loaded:
                raise ConflictError(f"未验证到 skill-creator 加载与官方打包调用；运行 {run_id}")
            artifacts = await container.artifacts.list_for_run(tenant_id, run_id)
            archive = next((a for a in artifacts if a.name == f"{name}.skill"), None)
            if archive is None and final_text(events):
                return SkillConversationReply(status="clarifying", reply=final_text(events)[:4000],
                    creatorRunId=run_id, creatorSourceRevision=package.source_revision)
            if archive is None or not packaged:
                raise ConflictError(f"未验证到 skill-creator 加载与官方打包调用；运行 {run_id}")
            _, content = await container.artifacts.download(tenant_id, archive.artifact_id)
            imported = import_skill(content, filename=archive.name)
            evaluation = next((a for a in artifacts if a.name == "evals.json"), None)
            if evaluation is None:
                raise ConflictError(f"Skill Creator 未发布测试用例，草稿未修改；运行 {run_id}")
            _, evaluation_bytes = await container.artifacts.download(
                tenant_id, evaluation.artifact_id
            )
            try:
                if len(evaluation_bytes) > 1024 * 1024:
                    raise ValueError("evaluation too large")
                evaluation_text = evaluation_bytes.decode("utf-8")
                evaluation_plan = _EvaluationPlan.model_validate_json(evaluation_text)
                if any(not case.prompt.strip() for case in evaluation_plan.evals):
                    raise ValueError("missing test prompts")
            except (ValueError, KeyError, TypeError):
                raise ConflictError(f"Skill Creator 的测试用例无效；运行 {run_id}") from None
            skill = imported.skill.model_copy(
                update={
                    "files": tuple(f for f in imported.skill.files if f.path != "evals/evals.json")
                    + (DraftSkillFile(path="evals/evals.json", content=evaluation_text),)
                }
            )
            return SkillConversationReply(
                status="ready",
                reply=(final_text(events) or "已生成 Skill，等待差异审阅。")[:4000],
                skill=skill,
                creatorRunId=run_id,
                creatorSourceRevision=package.source_revision,
                artifactIds=tuple(a.artifact_id for a in artifacts),
                artifactNames=tuple(a.name for a in artifacts),
            )
        except ValueError:
            await container.runs.cancel(tenant_id, run_id)
            raise ConflictError(f"Skill Creator 产物未通过安装校验；运行 {run_id}") from None
        except TimeoutError:
            await container.runs.cancel(tenant_id, run_id)
            raise ConflictError(f"Skill Creator 超时，草稿未修改；运行 {run_id}") from None
        except (asyncio.CancelledError, ConflictError):
            await container.runs.cancel(tenant_id, run_id)
            raise
        finally:
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
