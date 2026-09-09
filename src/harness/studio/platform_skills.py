"""Curated, offline-safe platform Skill packages with snapshot install semantics."""

from __future__ import annotations

import hashlib
import json

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
_ANTHROPIC_SKILLS_REVISION = "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f"
_ANTHROPIC_SKILLS_URL = (
    "https://github.com/anthropics/skills/blob/"
    f"{_ANTHROPIC_SKILLS_REVISION}/document-skills"
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


def default_platform_skill_catalog() -> PlatformSkillCatalog:
    """Return the reviewed v1 catalog. Packages contain no scripts or network dependency."""

    return PlatformSkillCatalog(
        revision=_CATALOG_REVISION,
        packages=(
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
            _package(
                package_id="office-docx",
                display_name="Word 文档创作",
                summary="用 python-docx 在沙箱内确定性生成并校验真实 .docx 文档。",
                tags=("办公", "Word", "docx"),
                evaluation_prompt=(
                    "请根据给定的调研要点生成一份带标题层级和数据表格的 Word 报告，"
                    "完成后重新解析文件核对标题、表格与图片结构，并报告校验结果。"
                ),
                skill=DraftSkill(
                    name="office-docx",
                    description=(
                        "Create and edit real .docx Word documents with python-docx in the "
                        "sandbox. Use when the user asks for a Word 文档、.docx 文件、报告交付物"
                        "或修改已有 docx。Covers headings, paragraphs, tables, images and styles"
                        " via deterministic Python code, then re-opens the file to verify "
                        "structure. Not for HTML/Markdown documents, PDF files, or pure text "
                        "answers."
                    ),
                    instructions="""# Word 文档创作（.docx）

用户需要交付或修改真实 Word 文件（.docx）时使用本流程。

1. 先确认文档目标、受众、章节结构、格式要求（字体、页边距、页眉页脚）和验收标准。
2. 前置探测：运行 `python3 -c "import docx"` 确认 python-docx 可用；不可用时先尝试在
   工作区虚拟环境安装，仍不可用则只交付内容大纲与结构建议，并明确说明尚未生成文件。
3. 生成必须通过确定性 Python 脚本完成：标题层级用内置 Heading 样式，正文段落、表格
   （含表头重复）、图片（指定宽度）和页码分别设置；不用文字模拟排版。
4. 生成后重新打开文件校验：标题层级顺序、表格行列数、图片数量、文档可打开性；
   校验失败必须修复后重新生成，不得直接交付。
5. 交付时列出文件路径、生成方式、校验结果和已知限制（如目录页需在 Word 中刷新）。

不得在没有工具证据时声称文件已生成；不得把模板建议描述成已完成的文档。
""",
                    files=(
                        DraftSkillFile(
                            path="references/docx-checklist.md",
                            content=(
                                "# docx 交付检查清单\n\n"
                                "- 文件能被 python-docx 重新打开且结构完整？\n"
                                "- 标题层级、表格行列、图片数量与要求一致？\n"
                                "- 样式统一（正文/标题字体、页边距、页码）？\n"
                                "- 输出是否说明生成方式、校验结果与限制？\n"
                            ),
                        ),
                        DraftSkillFile(
                            path="references/UPSTREAM.md",
                            content=(
                                "# 上游来源与修改声明\n\n"
                                "本包参考 anthropics/skills 仓库 document skills 公开描述的"
                                "“确定性脚本生成 + 重新解析校验”方法论，全部文本为平台原创"
                                "中文重写，未复制其 SKILL.md 文本或脚本。\n\n"
                                f"- 上游版本：`{_ANTHROPIC_SKILLS_REVISION}`\n"
                                "- 上游文件："
                                f"{_ANTHROPIC_SKILLS_URL}\n"
                                "- 许可证说明：上游 document skills 为 Anthropic 自定义许可；"
                                "本包不包含其受许可文本，平台原创内容以 Apache-2.0 提供。\n"
                                "- 修改：改为平台沙箱离线运行（python-docx），加入探测、"
                                "校验与证据化交付约束。\n"
                            ),
                        ),
                    ),
                ),
                source_url=_ANTHROPIC_SKILLS_URL,
                source_revision=_ANTHROPIC_SKILLS_REVISION,
            ),
            _package(
                package_id="office-xlsx",
                display_name="Excel 表格工作簿",
                summary="用 openpyxl 生成真实公式与类型化数据的 .xlsx 工作簿并复核。",
                tags=("办公", "Excel", "xlsx"),
                evaluation_prompt=(
                    "请根据给定的销售数据生成一个含汇总公式、冻结表头和数字格式的工作簿，"
                    "完成后重新打开抽查公式与关键单元格数值。"
                ),
                skill=DraftSkill(
                    name="office-xlsx",
                    description=(
                        "Create, read and edit Excel workbooks (.xlsx/.xlsm) and CSV files "
                        "with openpyxl in the sandbox. Use for Excel 表格、.xlsx 工作簿、"
                        "数据汇总、财务模型。Writes real formulas, typed cells, number "
                        "formats and frozen panes via Python code, then re-opens the "
                        "workbook to verify formulas and sampled cells. Not for pivot "
                        "tables requiring a recalculation engine or chart-only image "
                        "exports."
                    ),
                    instructions="""# Excel 表格工作簿（.xlsx）

用户需要交付、读取或修改真实 Excel 文件时使用本流程。

1. 先确认数据来源、工作表结构、计算规则、格式要求和验收标准。
2. 前置探测：运行 `python3 -c "import openpyxl"` 确认 openpyxl 可用；不可用时先尝试
   在工作区虚拟环境安装，仍不可用则只交付数据结构与公式设计，并说明尚未生成文件。
3. 生成必须通过确定性 Python 脚本完成：汇总与派生列用真实公式（SUM/AVERAGE/
   VLOOKUP 等），数值/日期/文本分列存储并设置数字格式，冻结首行表头，不做手工数。
4. 生成后重新打开文件校验：工作表数量、公式字符串、抽样单元格的值与类型、
   表头冻结设置。openpyxl 不执行重算，交付时须说明公式将在 Excel 打开时计算。
5. 交付时列出文件路径、生成方式、抽查结果和已知限制。

不得在没有工具证据时声称工作簿已生成；不得把估算值伪装成公式结果。
""",
                    files=(
                        DraftSkillFile(
                            path="references/xlsx-checklist.md",
                            content=(
                                "# xlsx 交付检查清单\n\n"
                                "- 文件能被 openpyxl 重新打开且工作表结构完整？\n"
                                "- 汇总使用真实公式而非硬编码数值？\n"
                                "- 数值、日期、文本类型与数字格式正确？\n"
                                "- 表头冻结、列宽等可读性设置到位？\n"
                                "- 输出是否说明重算限制与抽查结果？\n"
                            ),
                        ),
                        DraftSkillFile(
                            path="references/UPSTREAM.md",
                            content=(
                                "# 上游来源与修改声明\n\n"
                                "本包参考 anthropics/skills 仓库 document skills 公开描述的"
                                "“真实公式 + 类型化单元格 + 生成后复核”方法论，全部文本为"
                                "平台原创中文重写，未复制其 SKILL.md 文本或脚本。\n\n"
                                f"- 上游版本：`{_ANTHROPIC_SKILLS_REVISION}`\n"
                                "- 上游文件："
                                f"{_ANTHROPIC_SKILLS_URL}\n"
                                "- 许可证说明：上游 document skills 为 Anthropic 自定义许可；"
                                "本包不包含其受许可文本，平台原创内容以 Apache-2.0 提供。\n"
                                "- 修改：改为平台沙箱离线运行（openpyxl），加入探测、"
                                "复核与重算限制说明。\n"
                            ),
                        ),
                    ),
                ),
                source_url=_ANTHROPIC_SKILLS_URL,
                source_revision=_ANTHROPIC_SKILLS_REVISION,
            ),
            _package(
                package_id="office-pptx",
                display_name="PowerPoint 演示文稿",
                summary="用 python-pptx 生成真实 .pptx 文件并复核页数与内容。",
                tags=("办公", "PPT", "pptx"),
                evaluation_prompt=(
                    "请根据给定主题先列出幻灯片大纲，确认结构后生成含标题页、内容页和"
                    "演讲者备注的 .pptx 文件，并重新解析校验页数与文字。"
                ),
                skill=DraftSkill(
                    name="office-pptx",
                    description=(
                        "Create and edit PowerPoint .pptx files with python-pptx in the "
                        "sandbox. Use when the user asks for a PPT/PowerPoint 文件、.pptx "
                        "幻灯片文件，或修改已有 pptx。Builds slides from an approved "
                        "outline with layouts, placeholders, tables, images and speaker "
                        "notes via deterministic Python code, then re-opens the file to "
                        "verify slide count and text. For styled HTML slide decks use the "
                        "html-ppt skill instead."
                    ),
                    instructions="""# PowerPoint 演示文稿（.pptx）

用户需要交付真实 PowerPoint 文件（.pptx）时使用本流程；若用户要的是网页样式
HTML 幻灯片，应改用 html-ppt Skill 而不是本流程。

1. 先给出幻灯片大纲（每页标题与要点）供确认；页数和 信息密度 服从用途
   （汇报/评审/分享）。
2. 前置探测：运行 `python3 -c "import pptx"` 确认 python-pptx 可用；不可用时先尝试
   在工作区虚拟环境安装，仍不可用则只交付大纲与版式建议，并说明尚未生成文件。
3. 生成必须通过确定性 Python 脚本完成：使用版式占位符（标题/正文/两栏），表格与
   图片按占位符尺寸放置，每页写入演讲者备注；不用文字模拟幻灯片。
4. 生成后重新打开文件校验：页数、每页标题文本、备注是否存在、图片数量。
5. 交付时列出文件路径、页数、校验结果和已知限制（如复杂动画与主题配色需在
   PowerPoint 中调整）。

不得在没有工具证据时声称文件已生成；不得跳过大纲确认直接生成大体积演示文稿。
""",
                    files=(
                        DraftSkillFile(
                            path="references/pptx-checklist.md",
                            content=(
                                "# pptx 交付检查清单\n\n"
                                "- 文件能被 python-pptx 重新打开且页数一致？\n"
                                "- 每页标题与大纲一致，无占位符残留？\n"
                                "- 演讲者备注已写入？\n"
                                "- 图片清晰、不变形、尺寸符合版式？\n"
                                "- 输出是否说明校验结果与限制？\n"
                            ),
                        ),
                        DraftSkillFile(
                            path="references/UPSTREAM.md",
                            content=(
                                "# 上游来源与修改声明\n\n"
                                "本包参考 anthropics/skills 仓库 document skills 公开描述的"
                                "“大纲先行 + 占位符版式 + 重新解析校验”方法论，全部文本为"
                                "平台原创中文重写，未复制其 SKILL.md 文本或脚本。\n\n"
                                f"- 上游版本：`{_ANTHROPIC_SKILLS_REVISION}`\n"
                                "- 上游文件："
                                f"{_ANTHROPIC_SKILLS_URL}\n"
                                "- 许可证说明：上游 document skills 为 Anthropic 自定义许可；"
                                "本包不包含其受许可文本，平台原创内容以 Apache-2.0 提供。\n"
                                "- 修改：改为平台沙箱离线运行（python-pptx），加入与 "
                                "html-ppt 的边界说明与大纲确认步骤。\n"
                            ),
                        ),
                    ),
                ),
                source_url=_ANTHROPIC_SKILLS_URL,
                source_revision=_ANTHROPIC_SKILLS_REVISION,
            ),
            _package(
                package_id="office-pdf",
                display_name="PDF 文档处理",
                summary="用 pypdf 读取、合并、拆分 PDF 并提取文本与页数校验。",
                tags=("办公", "PDF", "pypdf"),
                evaluation_prompt=(
                    "请把给定的两个 PDF 合并为一个文件，报告总页数，并抽取其中包含"
                    "关键结论的页面文本作为证据。"
                ),
                skill=DraftSkill(
                    name="office-pdf",
                    description=(
                        "Read, merge, split and extract text from PDF files with pypdf in "
                        "the sandbox. Use for PDF 合并、拆分、页数统计、文本提取，或校验"
                        "生成 PDF 的页数与内容。Explains honestly when layout-faithful PDF "
                        "creation is not available and proposes a deterministic conversion "
                        "path instead. Not for OCR of scanned images or form-filling via "
                        "external services."
                    ),
                    instructions="""# PDF 文档处理

用户需要读取、合并、拆分 PDF，或从 PDF 提取文本/校验页数时使用本流程。

1. 先确认输入文件、期望输出（合并顺序、拆分范围、需要的页面）和验收标准。
2. 前置探测：运行 `python3 -c "import pypdf"` 确认 pypdf 可用；不可用时先尝试在
   工作区虚拟环境安装，仍不可用则只交付处理方案，并说明尚未处理文件。
3. 读取与抽取必须通过确定性 Python 脚本完成：页数、页面尺寸、书签、文本按页
   提取并标注页码；扫描件无文本层时如实说明，不臆造内容。
4. 合并与拆分后重新打开输出文件校验：页数一致、页面顺序正确、文本抽查可读。
5. 需要从零生成排版精美的 PDF 时，说明当前工具边界，改走 Markdown/HTML 或
   docx 内容加确定性转换的路径，并校验转换产物页数与文本。
6. 交付时列出输出文件路径、页数、抽取/校验证据和已知限制。

不得在没有工具证据时声称 PDF 已处理完成；不得把 OCR 猜测描述为原文。
""",
                    files=(
                        DraftSkillFile(
                            path="references/pdf-checklist.md",
                            content=(
                                "# PDF 处理检查清单\n\n"
                                "- 输出文件能被 pypdf 重新打开且页数一致？\n"
                                "- 文本提取标注了页码，可直接回查原文？\n"
                                "- 扫描件/无文本层的情况已如实说明？\n"
                                "- 生成类需求是否说明工具边界与转换路径？\n"
                            ),
                        ),
                        DraftSkillFile(
                            path="references/UPSTREAM.md",
                            content=(
                                "# 上游来源与修改声明\n\n"
                                "本包参考 anthropics/skills 仓库 document skills 公开描述的"
                                "“按页确定性处理 + 重新打开校验”方法论，全部文本为平台"
                                "原创中文重写，未复制其 SKILL.md 文本或脚本。\n\n"
                                f"- 上游版本：`{_ANTHROPIC_SKILLS_REVISION}`\n"
                                "- 上游文件："
                                f"{_ANTHROPIC_SKILLS_URL}\n"
                                "- 许可证说明：上游 document skills 为 Anthropic 自定义许可；"
                                "本包不包含其受许可文本，平台原创内容以 Apache-2.0 提供。\n"
                                "- 修改：改为平台沙箱离线运行（pypdf），加入扫描件与"
                                "排版生成边界说明。\n"
                            ),
                        ),
                    ),
                ),
                source_url=_ANTHROPIC_SKILLS_URL,
                source_revision=_ANTHROPIC_SKILLS_REVISION,
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
