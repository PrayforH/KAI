# Agent Builder 画布五阶段收敛设计

来源：《AXIS 开源生态对标与后续发展建议（2026-08）》（<https://my.feishu.cn/docx/JAjMdUX5goRipAxlJxocslSrnqd>）P1 "Builder 按五阶段收敛信息架构"。该节此前只存在于飞书建议中，仓库内没有对应设计；本文档补齐这一缺口，作为 Builder 画布收敛的实现依据。

## 目标

把 Studio Builder 当前"8 个平铺章节 + 两套并行心智模型（章节导航与生命周期条）+ 多个悬浮 rail/弹层"收敛为**一条五阶段主轴**，让"新建 Agent 到发布"变成一条有顺序、有出口条件、有阻塞原因的路径。同时落地 Agent Copilot 最小闭环（输出可分块接受的 BuilderPatch）。

不做的事情先说清楚（与飞书"定位与叙事"一致）：

- 不做自由节点画布、不做第四家编排框架；图视图只在多智能体/人工节点确实存在时以只读子图形式出现。
- Copilot 不直接发布、不直接修改生产版本；一切写入仍走 Draft → 门禁 → 不可变 Version。
- 前端不再硬编码任何运行时能力判断（呼应 P0"统一 Codex 兼容判断"），一律由服务端 RuntimeCapabilities 与 Compiler issues 驱动。

## 现状与问题

现状代码：`web/harness-console/src/components/agent-studio/agent-studio-workbench.tsx`（约 4100 行单文件）。

| 现状 | 问题 |
| --- | --- |
| `sectionNav` 平铺 8 章节：identity / model / prompt / orchestration / skills / capabilities / runtime / evaluation（`lib/agent-studio.ts` 的 `StudioSection`） | 无顺序、无出口条件；用户不知道"还差什么才能发布" |
| `lifecycleBar` 另有一套 5 段生命周期：草稿→预检→隔离试跑→版本→部署 | 与章节导航并行，两套心智模型互相解释 |
| 契约（`evaluateStudioDraft`）收在 `contractOpen` 悬浮 rail | 契约是编译与发布的锚点，却不是编辑主区的一等公民 |
| 测试与发布合在 `evaluation` 一个章节 | 试跑的"验证"语义和发布的"晋级"语义混在一起 |
| 首次进入无引导，默认落在 `capabilities` 章节 | 新用户第一步就面对确定性能力清单，偏离"先定目标与契约" |
| 运行时兼容性由前端硬编码（如 Codex 阻断文案） | 与服务端编译器结论漂移，直接误导用户（P0 已立项修复） |

## 五阶段信息架构

新主轴五阶段，章节按职责归位。`StudioSection` 的 8 个 id 降级为各阶段内部的子分区，Draft 数据结构与 API 不变——本次收敛只动信息架构与组件结构，不动数据模型。

| 阶段 | 归入的现有章节 | 内容 | 出口条件 |
| --- | --- | --- | --- |
| 1 目标与契约 | identity + model + 契约 rail | 显示名称/Agent ID/领域/场景说明；模型路由选择；输入/输出契约（终态、必需/禁止工具、预算） | `evaluateStudioDraft` ready；路由能力由 RuntimeCapabilities 判定兼容 |
| 2 能力 | capabilities + knowledge 型 MCP + orchestration 的角色清单 | 行动面：内置工具、MCP 工具（含 tool_search 建议）、Python 工具；事实面：知识库引用与知识型 MCP（收口为知识平面 connector）；委托面：Sub-Agent 角色引用 | 每个已声明能力都有权限策略覆盖；知识引用在租户与环境 allowlist 内 |
| 3 行为 | prompt + skills + orchestration 的编排语义 + runtime | System Prompt 五段结构（原则与协议）；Skills 业务 SOP（随版本固化）；Lead+Sub 委派规则（何时委派、收口人是谁）；执行档位、隔离级别、审批 allow/deny/ask | `promptSections` 满足 5 段；权限策略与阶段 2 声明一致 |
| 4 试跑 | 预检（check）+ Preview（TryRunPanel）+ 核心 eval 用例 | 静态门禁结果；真实 Preflight；Preview Sandbox（测试身份、1h TTL、过期与 stale 提示）；满足输出契约的首个试跑 | Preflight 通过 + 首个满足契约的试跑成功 |
| 5 发布 | evaluation 的发布面 + 版本历史 rail | 不可变 Bundle 打包；Eval/质量门禁；签名 Release 证据；test/canary/production 晋级与回滚；版本历史与引用 | 干净工作树门禁 + 发布证据齐全 |

能力分类术语（manifest 与 UI 对齐，详见 `docs/domain-agents.md`）：**Prompt+Skill 是 Agent 的行为身份（进版本），工具/知识/子 Agent 是 Agent 的治理装配（进引用）**。每个能力条目带绑定语义徽章：`随版本固化`（Skill，显示内容哈希）、`运行时引用 · 快照`（知识库，显示快照版本）、`运行时引用 · 凭据托管`（MCP）。知识型 MCP 定位为知识平面的 connector，不出现在工具清单（消除 `category` 补丁）。

要点：

- **契约 rail 升级为阶段 1 主区**。头部只保留一个契约状态 chip，点击回到阶段 1，不再开悬浮抽屉。
- **两套心智模型合一**。`lifecycleBar` 取消；五阶段 stepper 直接承担生命周期显示——每阶段一个状态（complete / active / pending / blocked），blocked 时在主区显示当前阻塞原因与跳转动作（呼应 P1"把当前步骤与阻塞原因放在页面主区"）。
- **试跑与发布拆开**。"验证"（阶段 4）与"晋级"（阶段 5）分家，消除"测试与发布"混排。
- **受限图视图**。仅当 `subagents.length > 0` 或存在人工审批节点时，阶段 3 提供只读子图视图（Lead→Sub/审批节点→收口），复用现有 orchestration 数据，不引入节点画布编辑器。

## 导航与首次引导

- 五阶段 stepper 为全局主轴，阶段内子分区用次级 tab；`?section=` 旧链接映射到 `阶段+子分区`。
- 新建流程：`NewAgentDialog` 只从服务端 Catalog templates 选择模板（呼应 P0"恢复服务端模板入口"，禁止前端默认草稿覆盖脚手架），创建后落入阶段 1 并给出可跳过的首次引导：目标与契约 → 能力 → 行为 → 试跑 → 发布。
- 每阶段头部固定回答三个问题：这一步决定什么 / 什么算完成 / 现在阻塞是什么。

## Agent Copilot 最小闭环

- 形态：常驻阶段侧边的 Copilot 抽屉，任何阶段可唤起；输入为当前 Draft + 当前阶段上下文。
- 输出：`BuilderPatch { stage, ops: [{ block, op, before, after, rationale }] }`，按块列出差异。
- 接受方式：**分块接受**——每块可单独接受/拒绝/全部接受；接受后经现有 `updateDraft` 路径写入 Draft，未接受块不落盘；提供按块回滚。
- 边界：Patch 只进入 Draft；发布仍必须走阶段 5 的门禁与晋级；Copilot 不提供绕过 RuntimeCapabilities 的选项。
- 埋点：记录每块接受/拒绝与后续回滚，产出验收指标 Patch Acceptance。

## 组件与代码结构

把 4100 行的 workbench 按阶段拆分，数据流仍收敛在单一 Draft reducer：

```text
components/agent-studio/
  agent-studio-workbench.tsx        # 仅保留骨架：stepper、阶段路由、全局状态
  builder/
    goal-contract.tsx               # 阶段 1
    capabilities.tsx                # 阶段 2
    behavior.tsx                    # 阶段 3（含只读子图视图）
    trial.tsx                       # 阶段 4（吸收 TryRunPanel 主区化）
    publish.tsx                     # 阶段 5（吸收版本历史 rail）
    copilot-drawer.tsx              # Copilot 抽屉 + BuilderPatch 审阅
    builder-patch.ts                # BuilderPatch 类型、diff、按块应用/回滚
```

- `lib/agent-studio.ts`：`StudioSection` 8 值联合改为 5 阶段 + 子分区 id；`evaluateStudioDraft`、`StudioDraft`、服务端 API 契约不变。
- 阶段 1/2 的运行时与能力可用性一律读取服务端 RuntimeCapabilities（P0 交付物 v0 覆盖 Claude/Codex 两行能力矩阵），删除前端硬编码分支。
- 未保存离开守卫、版本冲突、只读角色等既有横切行为保留在骨架层，阶段组件不重复实现。

## 迁移与兼容

- Draft/版本/发布 API 零改动；已有草稿打开后按字段自动归入新阶段，无数据迁移。
- 旧 `?section=` 链接与文档/手册中的章节名做映射表（identity→阶段 1，evaluation→阶段 4/5 等），在《Agent Studio 产品使用手册》对应章节随版本一并更新。
- 灰度顺序：先拆骨架与阶段组件（纯重构，行为等价），再切五阶段导航，最后上 Copilot 抽屉；三步各自可独立回滚。

## 验收

| 指标 | 定义 | 来源 |
| --- | --- | --- |
| Time to First Successful Run | 新建 Agent 到首个满足输出契约的试跑的中位时间，目标较现状下降 | 飞书建议·验收指标 |
| First Preflight Pass Rate | 首次真实预检即通过的 Draft 比例上升 | 飞书建议·验收指标 |
| Patch Acceptance | Copilot Patch 被接受的块比例及后续回滚率 | 飞书建议·验收指标 |
| 导航收敛 | 章节数 8→5，生命周期条与章节导航合并为一套状态 | 本设计 |
| 契约一等公民 | 契约进入阶段 1 主区，契约 rail 悬浮抽屉下线 | 本设计 |
| 零前端运行时硬编码 | 运行时兼容判断全部来自 RuntimeCapabilities/Compiler issues | P0 契约测试 |
| 首次引导 | 新建→模板→阶段 1 引导路径可跳过且不覆盖服务端脚手架 | P0 模板入口 |
