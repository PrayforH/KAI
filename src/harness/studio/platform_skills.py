"""Curated, offline-safe platform Skill packages with snapshot install semantics."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache

from harness.core.errors import ConflictError, NotFoundError
from harness.evals.suite import EvalCase, EvalExpectation
from harness.studio.models import (
    DraftSkill,
    DraftSkillFile,
    DraftSkillSource,
    ImportedSkill,
    PlatformSkillCatalog,
    PlatformSkillPackage,
)

_CATALOG_REVISION = 1
_SOURCE_REVISION = "platform-skills-v1"
_SOURCE_ROOT = (
    "https://github.com/PrayforH/agent-studio/blob/main/"
    "src/harness/studio/platform_skills.py"
)
_OPENAI_SKILL_CREATOR_REVISION = "49f948faa9258a0c61caceaf225e179651397431"
_OPENAI_SKILL_CREATOR_URL = (
    "https://github.com/openai/skills/blob/"
    f"{_OPENAI_SKILL_CREATOR_REVISION}/skills/.system/skill-creator/SKILL.md"
)


def draft_skill_content_hash(skill: DraftSkill) -> str:
    payload = json.dumps(
        skill.model_dump(mode="json", by_alias=True, exclude={"source"}),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _package(
    *,
    package_id: str,
    display_name: str,
    summary: str,
    tags: tuple[str, ...],
    skill: DraftSkill,
    evaluation_prompt: str,
    source_url: str | None = None,
    source_revision: str = _SOURCE_REVISION,
    license_name: str = "Apache-2.0",
) -> PlatformSkillPackage:
    digest = draft_skill_content_hash(skill)
    resolved_source_url = source_url or f"{_SOURCE_ROOT}#{package_id}"
    source = DraftSkillSource(
        packageId=package_id,
        packageRevision=1,
        sourceUrl=resolved_source_url,
        sourceRevision=source_revision,
        license=license_name,
        contentHash=digest,
    )
    return PlatformSkillPackage(
        packageId=package_id,
        revision=1,
        displayName=display_name,
        summary=summary,
        tags=tags,
        compatibleRuntimes=("claude-agent-sdk", "codex-app-server"),
        license=license_name,
        sourceUrl=resolved_source_url,
        sourceRevision=source_revision,
        contentHash=digest,
        riskLevel="low",
        skill=skill.model_copy(update={"source": source}),
        evaluationCases=(
            EvalCase(
                id=f"skill-{package_id}-happy",
                tags=("happy", f"skill:{package_id}"),
                prompt=evaluation_prompt,
                expect=EvalExpectation(
                    terminalStatuses=("succeeded",),
                    maxDurationSeconds=120,
                ),
            ),
        ),
    )


_VENDORED_DISPLAY = {
    "minimax-docx": ("Word 文档（MiniMax）", ("办公", "Word", "docx")),
    "minimax-xlsx": ("Excel 表格（MiniMax）", ("办公", "Excel", "xlsx")),
    "minimax-pdf": ("PDF 文档（MiniMax）", ("办公", "PDF", "pdf")),
    "pptx-generator": ("PowerPoint 演示（MiniMax）", ("办公", "PPT", "pptx")),
    "color-font-skill": ("PPT 配色与字体", ("办公", "PPT", "设计")),
    "design-style-skill": ("PPT 设计风格", ("办公", "PPT", "设计")),
    "ppt-editing-skill": ("PPTX 模板编辑", ("办公", "PPT", "编辑")),
    "slide-making-skill": ("幻灯片制作", ("办公", "PPT", "制作")),
    "skill-creator": ("Skill 创建与评测", ("Skill", "创建", "评测")),
    "mcp-builder": ("MCP 服务开发", ("MCP", "开发", "集成")),
    "internal-comms": ("内部沟通写作", ("写作", "沟通", "企业")),
    "theme-factory": ("主题与配色工厂", ("设计", "主题", "配色")),
    "frontend-design": ("前端界面设计", ("设计", "前端", "UI")),
    "web-artifacts-builder": ("Web Artifact 构建", ("前端", "Artifact", "React")),
    "webapp-testing": ("Web 应用测试", ("测试", "Playwright", "前端")),
    "canvas-design": ("平面视觉设计", ("设计", "海报", "视觉")),
    "algorithmic-art": ("生成式艺术", ("设计", "生成艺术", "p5.js")),
}

_VENDORED_EVALUATION = {
    "minimax-docx": "请生成一份含标题层级与数据表格的 Word 文档，并在生成后重新解析文件核对结构。",
    "minimax-xlsx": "请生成含汇总公式与数字格式的工作簿，并在生成后重新打开抽查公式与关键单元格。",
    "minimax-pdf": "请生成一份带封面与正文设计的 PDF，并报告页数与版面结果。",
    "pptx-generator": "请先用 PptxGenJS 生成含封面、目录与内容页的演示文稿，再重新解析核对页数。",
    "color-font-skill": "请为一个演示文稿选择配色与字体搭配，说明选择依据并保持整套一致。",
    "design-style-skill": "请为演示文稿选定一套统一的设计风格（圆角/间距规则）并说明映射关系。",
    "ppt-editing-skill": "请在保留模板布局的前提下更新演示文稿内容，并说明改动范围。",
    "slide-making-skill": "请根据给定材料规划并制作一套幻灯片，先给出大纲结构。",
    "skill-creator": "请把一段重复流程整理成一个职责单一、触发条件明确的 Agent Skill，并给出评测用例。",
    "mcp-builder": "请为一个 HTTP API 设计 MCP 工具集，说明工具划分、输入契约与错误处理。",
    "internal-comms": "请撰写一份包含进展、计划与问题的内部状态更新，语言简洁面向管理层。",
    "theme-factory": "请为一份已有文档套用统一主题，说明所选配色与字体，并保持可读性。",
    "frontend-design": "请设计一个界面方案，给出排版、配色与组件选择，避免模板化观感。",
    "web-artifacts-builder": "请规划一个含状态管理的多组件前端 Artifact，说明组件拆分与数据流。",
    "webapp-testing": "请为本地 Web 应用设计一组端到端交互测试，说明断言与失败定位方式。",
    "canvas-design": "请设计一张静态视觉作品，说明构图、配色与字体选择，并说明输出方式。",
    "algorithmic-art": "请设计一个基于 p5.js 的生成艺术方案，说明参数空间与随机种子控制。",
}

_VENDORED_SKILLS_REVISION = _SOURCE_REVISION


@lru_cache(maxsize=1)
def _vendored_packages() -> tuple[PlatformSkillPackage, ...]:
    """Build vendored catalog entries once; hashing multi-MB assets is costly."""

    from harness.studio.vendor_skills import load_vendored_skills

    return tuple(_vendored_package(skill) for skill in load_vendored_skills())


def _vendored_package(skill: DraftSkill) -> PlatformSkillPackage:
    """Wrap a vendored Skill directory as a reviewed catalog package.

    Vendored packages ship upstream files verbatim (including the upstream
    LICENSE) and are attributed to the pinned upstream revision. Any package
    carrying executable files is catalogued at ``review`` risk so operators can
    see that it can run code in the sandbox.
    """

    from harness.studio.vendor_skills import VENDORED_SOURCES

    source_url, source_revision, license_name = VENDORED_SOURCES[skill.name]
    display_name, tags = _VENDORED_DISPLAY[skill.name]
    digest = draft_skill_content_hash(skill)
    source = DraftSkillSource(
        packageId=skill.name,
        packageRevision=1,
        sourceUrl=source_url,
        sourceRevision=source_revision,
        license=license_name,
        contentHash=digest,
    )
    has_scripts = any(
        file.path.startswith("scripts/") or file.path.endswith((".py", ".js", ".mjs", ".sh"))
        for file in skill.files
    )
    return PlatformSkillPackage(
        packageId=skill.name,
        revision=1,
        displayName=display_name,
        summary=f"{skill.description[:180]}（上游 {license_name}，随包附带许可证原文）",
        tags=tags,
        compatibleRuntimes=("claude-agent-sdk", "codex-app-server"),
        license=license_name,
        sourceUrl=source_url,
        sourceRevision=source_revision,
        contentHash=digest,
        riskLevel="review" if has_scripts else "low",
        findings=("包含沙箱内可执行文件：scripts/",) if has_scripts else (),
        skill=skill.model_copy(update={"source": source}),
        evaluationCases=(
            EvalCase(
                id=f"skill-{skill.name}-happy",
                tags=("happy", f"skill:{skill.name}"),
                prompt=_VENDORED_EVALUATION[skill.name],
                expect=EvalExpectation(
                    terminalStatuses=("succeeded",),
                    maxDurationSeconds=120,
                ),
            ),
        ),
    )


def default_platform_skill_catalog() -> PlatformSkillCatalog:
    """Return the reviewed catalog of first-party and vendored Skill packages."""

    return PlatformSkillCatalog(
        revision=_CATALOG_REVISION,
        packages=(
            *_vendored_packages(),
            _package(
                package_id="evidence-reporting",
                display_name="证据化报告",
                summary="把事实、判断、缺口和结论整理成可追溯的结构化报告。",
                tags=("报告", "证据", "质量"),
                evaluation_prompt=(
                    "请根据两份存在局部冲突的材料生成报告，明确区分证据、判断和缺口。"
                ),
                skill=DraftSkill(
                    name="evidence-reporting",
                    description=(
                        "Produce structured reports that separate evidence, analysis, and gaps."
                    ),
                    instructions="""# 证据化报告

在用户需要研究、审查、分析或总结材料时使用本流程。

1. 先确认报告目标、受众、范围和交付格式。
2. 为每项关键结论保留来源；把直接证据、分析判断和未知信息分开。
3. 同一结论存在冲突证据时并列呈现，不用语气掩盖不确定性。
4. 输出按“结论摘要、关键证据、分析、限制与缺口、下一步”组织。
5. 交付前逐项执行随包附带的检查清单。

不得声称读取了未实际访问的来源，也不得把建议描述成已经执行的动作。
""",
                    files=(
                        DraftSkillFile(
                            path="references/report-checklist.md",
                            content=(
                                "# 报告检查清单\n\n"
                                "- 关键结论是否都有来源或明确标记为判断？\n"
                                "- 是否说明时间范围、材料范围和缺失输入？\n"
                                "- 是否保留冲突证据与不确定性？\n"
                                "- 最终交付是否符合用户要求的结构和格式？\n"
                            ),
                        ),
                    ),
                ),
            ),
            _package(
                package_id="research-synthesis",
                display_name="多源研究归纳",
                summary="对已有材料进行分层检索、交叉比对和引用归纳，不依赖外网。",
                tags=("研究", "检索", "归纳"),
                evaluation_prompt=(
                    "请归纳工作区中已有的多份材料，列出共识、分歧、来源和待补证据。"
                ),
                skill=DraftSkill(
                    name="research-synthesis",
                    description="Synthesize multiple available sources with traceable comparisons.",
                    instructions="""# 多源研究归纳

在工作区、知识库或已授权工具提供多份材料时使用本流程。

1. 把研究问题拆成可验证的子问题，并列出需要的证据类型。
2. 先检索已有材料，再决定是否需要额外工具；没有联网能力时不得反复尝试公网访问。
3. 优先使用原始材料，并记录来源名称、日期和定位信息。
4. 对关键事实至少进行一次交叉检查；只有单一来源时明确说明。
5. 将共识、分歧、证据缺口和推断分别汇总，再回答原始问题。

只报告实际找到的内容。材料不足时输出缺口清单和最小补充建议。
""",
                    files=(
                        DraftSkillFile(
                            path="references/source-matrix.md",
                            content=(
                                "# 来源矩阵\n\n"
                                "| 子问题 | 来源 | 日期 | 直接证据 | 支持/反对 | 可信度说明 |\n"
                                "|---|---|---|---|---|---|\n"
                                "\n重要结论至少填写一行；冲突来源分别占一行。\n"
                            ),
                        ),
                    ),
                ),
            ),
            _package(
                package_id="delivery-verification",
                display_name="交付结果核验",
                summary="在最终回复或文件交付前检查完整性、可打开性与需求覆盖。",
                tags=("核验", "交付", "SOP"),
                evaluation_prompt=(
                    "请在提交结果前按需求逐项核验，并说明通过项、失败项和核验依据。"
                ),
                skill=DraftSkill(
                    name="delivery-verification",
                    description=(
                        "Verify deliverables against the request before reporting completion."
                    ),
                    instructions="""# 交付结果核验

在准备宣布任务完成前使用本流程。

1. 把用户要求转换为逐项验收清单，包括内容、格式、文件名和限制条件。
2. 核对每项要求是否有对应结果；未完成项不得用笼统总结遮盖。
3. 对生成文件执行可用的确定性检查，例如存在性、大小、格式解析或渲染检查。
4. 检查输出中是否残留占位符、调试信息、敏感信息或未经验证的断言。
5. 只在必需项通过后报告完成；否则说明失败项、证据和可执行的修复步骤。

核验不等于重新解释任务。优先使用平台已有工具和测试结果作为证据。
""",
                    files=(
                        DraftSkillFile(
                            path="references/acceptance-template.md",
                            content=(
                                "# 验收记录\n\n"
                                "| 要求 | 结果位置 | 核验方式 | 状态 |\n"
                                "|---|---|---|---|\n"
                                "| 示例要求 | 输出或文件 | 测试/解析/人工检查 | 通过/失败 |\n"
                            ),
                        ),
                    ),
                ),
            ),
            _package(
                package_id="document-spreadsheet-production",
                display_name="文档与表格制作",
                summary="规范文档与表格的制作、工具生成、渲染检查和交付流程。",
                tags=("文档", "表格", "交付物"),
                evaluation_prompt=(
                    "请制定一份报告和配套数据表的制作方案，明确由确定性工具完成生成与验证，"
                    "并给出可核验的交付证据。"
                ),
                skill=DraftSkill(
                    name="document-spreadsheet-production",
                    description=(
                        "Plan and verify document and spreadsheet deliverables while delegating "
                        "file generation and conversion to deterministic tools."
                    ),
                    instructions="""# 文档与表格制作

在用户要求交付文档、报告、工作簿、表格或格式转换结果时使用本流程。

1. 先确认受众、内容范围、文件格式、版式要求、数据来源和验收标准。
2. 将内容结构、数据结构和样式约束分别列清；表格中的计算规则应使用可复核的公式或代码。
3. 实际创建、转换和修改文件必须调用平台提供的确定性工具或脚本，不得用文字模拟文件生成。
4. 生成后使用解析、公式检查、渲染预览或其他可用工具核对结构、数值和视觉结果。
5. 交付时列出文件、生成方式、验证方式和仍存在的限制。

如果运行环境没有合适的文件工具，只能提供内容大纲、数据模型或制作方案，并明确说明尚未生成文件。不得在没有工具证据时声称文件已成功创建或转换。
""",
                    files=(
                        DraftSkillFile(
                            path="references/artifact-quality-matrix.md",
                            content=(
                                "# 交付物质量矩阵\n\n"
                                "| 交付物 | 生成方式 | 必需检查 | 交付证据 |\n"
                                "|---|---|---|---|\n"
                                "| 文档 | 文档生成工具 | 结构解析、分页与渲染预览 | "
                                "文件路径与检查结果 |\n"
                                "| 表格 | 表格工具或代码 | 公式、类型、关键汇总与可打开性 | "
                                "文件路径与抽查结果 |\n"
                                "| 格式转换 | 转换器 | 页数/工作表数、内容完整性与视觉差异 | "
                                "输入输出及验证记录 |\n"
                            ),
                        ),
                    ),
                ),
            ),
            _package(
                package_id="skill-authoring-quality",
                display_name="Skill 创建与质检",
                summary="基于 OpenAI skill-creator 的离线适配版，创建并审查可验证的 Skill。",
                tags=("Skill", "创建", "质检"),
                evaluation_prompt=(
                    "请审查一个职责过宽、包含凭据且缺少触发条件的 Skill 草稿，列出问题并给出"
                    "合规的重构结构。"
                ),
                skill=DraftSkill(
                    name="skill-authoring-quality",
                    description=(
                        "Create or review focused Agent Skills with clear triggers, "
                        "safe resources, "
                        "and testable quality criteria."
                    ),
                    instructions="""# Skill 创建与质检

在创建、重构或评审 Agent Skill 时使用本流程。

1. 用 description 明确可观察的触发场景和适用边界，避免“万能助手”式描述。
2. 一个 Skill 只承载一套可复用 SOP；确定性规则放代码或 Policy，凭据和环境地址放受管配置。
3. SKILL.md 保留完成任务所需的步骤，将长资料放 references；可重复且确定性的动作放
   scripts，模板资源放 assets。
4. 指令只描述可观察行为、输入输出和验收方式，不要求或暴露模型隐藏思维过程。
5. 检查路径安全、依赖、许可证、敏感信息、外部网络和副作用边界。
6. 至少提供一个正常场景和一个边界或失败场景，验证触发、输出契约与失败行为。

质检未通过时应列出具体问题和最小修改建议，不得仅给出“可以使用”的笼统结论。
""",
                    files=(
                        DraftSkillFile(
                            path="references/skill-review-checklist.md",
                            content=(
                                "# Skill 评审清单\n\n"
                                "- description 是否明确说明何时使用？\n"
                                "- 职责是否单一，且没有把确定性规则伪装成提示词？\n"
                                "- references、scripts、assets 是否各自承担正确职责？\n"
                                "- 是否不含凭据、私有地址、租户数据和隐藏思维要求？\n"
                                "- 网络、依赖、副作用和失败行为是否明确？\n"
                                "- 是否包含正常与边界评测场景？\n"
                            ),
                        ),
                        DraftSkillFile(
                            path="references/UPSTREAM.md",
                            content=(
                                "# 上游来源与修改声明\n\n"
                                "本 Skill 基于 OpenAI `skill-creator` 的方法进行中文化、精简和"
                                "离线运行适配，未复制其脚本。\n\n"
                                f"- 上游版本：`{_OPENAI_SKILL_CREATOR_REVISION}`\n"
                                f"- 上游文件：{_OPENAI_SKILL_CREATOR_URL}\n"
                                "- 许可证：Apache License 2.0\n"
                                "- 修改：移除 Codex 专用初始化脚本和 UI 元数据生成步骤，加入"
                                "平台评测、凭据与治理边界。\n"
                            ),
                        ),
                    ),
                ),
                source_url=_OPENAI_SKILL_CREATOR_URL,
                source_revision=_OPENAI_SKILL_CREATOR_REVISION,
            ),

        ),
    )


def platform_skill_package(package_id: str, package_revision: int) -> PlatformSkillPackage:
    catalog = default_platform_skill_catalog()
    package = next((item for item in catalog.packages if item.package_id == package_id), None)
    if package is None:
        raise NotFoundError(f"Platform Skill package not found: {package_id}")
    if package.revision != package_revision:
        raise ConflictError(
            f"Platform Skill package revision changed: expected {package_revision}, "
            f"current {package.revision}"
        )
    return package


def imported_platform_skill(package: PlatformSkillPackage) -> ImportedSkill:
    return ImportedSkill(
        skill=package.skill,
        sourceContentHash=package.content_hash,
        riskLevel=package.risk_level,
        findings=package.findings,
        warnings=(
            f"已从平台 Skill 包 {package.package_id}@{package.revision} 导入草稿快照",
        ),
    )
