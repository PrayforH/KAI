# Web 端 Agent 产品 HITL 调研

调研日期：2026-09-16
调研对象：ChatGPT（Agent / Atlas）、Claude（Code 权限体系）、Kimi（OK Computer / Plan Mode）、豆包（任务模式）、Grok（Build / Bot）、Manus、Cursor、Gemini、Perplexity Comet
目的：找出值得借鉴到 AXIS 的 human-in-the-loop 设计

> 说明：产品侧细节来自官方文档与多篇实测/分析文章，部分是二手转述；带具体百分比的数字已在文中标注来源，落地前建议对关键产品做一次亲测复核。

---

## 0. TL;DR：最值得借鉴的八件事

1. **审批不是二选一，而是"这一次 / 这个会话 / 永远"三档**。ChatGPT、Cursor、Comet、Claude Code 全都是三档起步。AXIS 现在只有 `approved | rejected`，导致同一个安全命令被反复追问。
2. **审批弹窗本身正在被淘汰，改为"风险分级 + 分类器"。** Anthropic 实测 Claude Code 的权限弹窗约 93–97% 最终被批准——也就是说逐次询问大部分是走过场的仪式感开销。Anthropic 的 auto mode 分类器在 1053 人测试中拦住了 89% 的危险命令，而人在时间压力下点击只拦住 13.6%。结论：**默认放行低风险，只在高风险处停下来**。
3. **拒绝要带理由，并且理由要回到模型。** 现在的 HITL 实现普遍只回 `rejected`，模型不知道为什么，下一轮继续犯同样的错。正确的做法是把拒绝理由作为反馈注入 agent 上下文。
4. **"允许并修改参数"是被验证的高价值动作。** AG-UI 的 interrupt 协议里已经预留了 `editedArgs`。用户把命令改一行再放行，比"拒绝 — 让模型重试 — 再弹一次"高效得多。
5. **Plan gate（先出计划、批准后执行）是当前消费者产品最明确的"人机契约点"。** Kimi OK Computer 先列 11–14 项 todo 再执行；Kimi Code 和 Grok Build 都把 plan 审批做成**即使开 YOLO/auto 也不能跳过**的硬门槛。
6. **审批要从"对话内卡片"升格为"队列 + 路由 + 升级"。** Manus 把 take over 做成可配置通知；企业级框架的一致结论是：按风险路由、超时默认拒绝、支持委派、SLA 到点自动升级。
7. **"接管"是比"批准"更重要的原语。** 豆包、Manus、ChatGPT Atlas、Grok Bot 都提供暂停/接管/归还。豆包甚至把"登录、验证码、付款、发送消息、发布内容"列为必须人工确认——这不是推理问题，是**渠道依赖**问题（人必须亲自进那个通道）。
8. **中途提问（elicitation / ask_user）是目前最大的空白。** MCP 规范化了表单模式和 URL 模式，AG-UI 有 `reason: "input_required"` + `responseSchema`。AXIS 完全没有这个能力。

---

## 1. 各家做法拆解

### 1.1 OpenAI — ChatGPT Agent / Atlas

- 产品哲学明确：**"在具有现实后果的动作之前显式征求许可"**。
- 交互四件套：
  - **暂停 / 打断 / 接管**：任何时刻可以停下，或者自己把下一步手动做掉。
  - **审批检查点（approval checkpoints）**：训练模型在重要动作前询问。
  - **实时叙述（live narration）**：在动作发生前展示将要进行的步骤与预期结果。
  - **自定义指令**：用户可以预先写规则（偏好的来源、必须的步骤、哪些地方必须审批）。
- **Watch Mode**：在敏感站点（如银行）强制标签页保持活跃；用户切走则自动暂停。
- **Logged-out mode**：不使用已有 cookie，不在未经明确批准的情况下登录任何账户——用于研究类任务降低风险。
- 能力边界写得很死：不能跑代码、不能下载文件、不能装扩展、不能读密码/自动填充、agent mode 访问的页面不进浏览历史。
- **值得借鉴的点**：
  - "把用户可写的审批规则"作为一等配置（AXIS 有 policy rule，但没有给终端用户写规则的入口）。
  - Watch Mode 的思路——**把"人在看"作为运行时前置条件**，而不只是事后批准。
  - 明确的能力边界清单比模糊的"谨慎使用"更有用。
- **批评**：有研究指出 ChatGPT agent mode 的"权限"宣传与实现存在落差——开放网络侧的权限策略实际上没有给用户留下批准/拒绝的机会，用户被迫在"每条都批"（隐私疲劳）和"LLM 自动审查"（不透明）之间二选一。

### 1.2 Anthropic — Claude Code 权限体系（目前最完整的一套）

权限模式谱系：`default(manual)` / `acceptEdits` / `plan` / `auto` / `dontAsk` / `bypassPermissions`。

最值得研究的三个机制：

1. **决策优先级是"deterministic 规则优先于概率分类器"**：`deny → ask → allow`，首个匹配生效（与规则具体程度无关）；任一作用域的 deny 都能压过其他作用域的 allow。这是防止"分类器被绕过"的关键结构。
2. **Plan mode 被建模为"可回退的权限状态转换"**：进入时保存 `prePlanMode`，退出时恢复。踩过的坑是——退出时没能恢复原有权限状态。这提醒 AXIS：plan gate 不要做成独立权限系统，要做成状态机的临时态。
3. **提示词可处理性（promptability）**：headless/后台任务遇到需要审批的调用，不应该挂起等待一个没人能回答的弹窗——要么自动拒绝，要么升级到有人的会话。（v2.1.186 之后的修正是：后台子 agent 把弹窗**上浮到父会话**；只有在确实没有可升级对象时才自动拒绝。）

已知反模式（都值得抄进 AXIS 的设计评审清单）：
- 把远端审批当成 UI 问题而不是**持久的运行时对象**（需要过期、取消、稳定 request id）。
- 持久化过宽的 shell 前缀规则（如 `Bash(*)`）导致权限蔓延。
- 低优先级的 allow 规则被前面的 ask/deny 规则遮蔽（unreachable rule）。
- 把"弹窗展示过"当成"有了有效审批结论"。
- 每个提示必须以**唯一终态**结束：approved / denied / cancelled / expired。

### 1.3 Kimi — OK Computer 与 Plan Mode

OK Computer（kimi.com 通用 agent 模式）：
- 收到任务后**先做项目管理**：拆成 11–14 项 todo，展示完整规划，再开始执行；界面显示"当前进度 N/N"逐项推进。
- 工作界面是**左聊天 + 右虚拟电脑**双栏，操作过程实时可见。
- 这条"先列清单再动手"是消费者产品里最容易理解的人机契约，成本极低、收益很高。

Kimi Code CLI 的 Plan Mode：
- `EnterPlanMode` 进入只读状态（只能 Glob/Grep/ReadFile，写入仅限 plan 文件）。
- `ExitPlanMode` 时把 plan 呈现给用户审批，**批准后才执行**。
- 用户可 approve / reject / revise；agent 可通过 `options` 参数提出 2–3 个备选方案（"Approve"/"Reject"/"Revise" 三个 label 由系统保留）。
- **关键设计**：即使 `--yolo` / AFK / auto 模式，plan 退出审批依然不被跳过；auto 模式只跳过普通工具调用的审批。也就是说 **plan gate 是比 tool gate 更高一层的、不可绕过的契约**。
- `AskUserQuestion` 是另一个独立机制：一轮内提 2–4 选项的结构化选择题，用于消歧和方案选择——**和 plan 审批是两回事**。

### 1.4 豆包 — 任务模式 / 操作电脑

- 模式从"快速/思考/专家"演进为"快速/专家/任务"，任务模式走"拆解 → 规划 → 工具调用 → 结果交付"全链路。
- **操作电脑**需要显式授权（Mac 要开辅助功能 + 录屏录音两项系统权限），之后用户可实时查看过程、**随时暂停/接管/终止**。
- **强制确认点**：涉及登录、验证码、付款、发送消息、发布内容时**必须人工确认**；删除操作默认禁止；改文件前先备份。
- 推荐的指令结构是"目标 + 输入 + 操作范围 + 禁止事项 + 输出格式 + 验收条件"，并允许用户**预先声明哪些步骤必须先确认**。
- 分析文章里最有价值的一条设计哲学：豆包把"**不可靠当作默认前提来设计**"，因此核心动作都围绕"降低验收成本"——待确认机制、来源标注、权限边界、技能固化。
- 另一篇企业视角的论述给了 HITL 的经济学基础：**15 步、每步 95% 准确率的流程，整体成功率只有 46%**（0.95¹⁵ ≈ 0.463）。所以复杂任务应该拆成独立 Skill，并在步骤之间**设置显式的人工确认点**。

### 1.5 Grok — Build / Bot

Grok Build 权限模式（官方文档，结构最清晰）：

| 模式 | 行为 | 进入方式 |
|---|---|---|
| **Ask**（默认） | 非白名单的一切都弹窗问 | — |
| **Auto** | 分类器自动批准安全工具，危险的仍可能弹窗 | `/auto`, `Shift+Tab` |
| **Always-approve** | 自动批准所有工具调用（deny 规则仍然生效） | `/always-approve`, `Ctrl+O`, `Shift+Tab` |

- 支持 allow/deny 规则（如 `deny bash rm -rf *`）。
- **Plan mode 的计划审批 UI 在 auto / always-approve 下也不跳过。**
- **记忆化的"总是允许"在遇到危险模式（`rm`、`git push`）时仍然弹窗**——这是一条很好的规则：允许记忆，但给危险模式设硬豁免。
- `/goal` 自主模式仍保留 `/goal status | pause | resume | clear`。

Grok Bot（持久化 agent）的界面经验：
- 暴露：Bot 在场感、执行预览、结构化 widget、transcript、**human takeover 控件**。
- **接管模式**会把电脑全屏打开，让人介入后归还控制权。
- **刻意不默认展示每一个内部步骤**——研究发现只要露出一个步骤，用户就会要求看到整条 trace。
- **结构化输出天然形成审批点**：比如邮件渲染成"待发送对象"，旁边就是 Send / Discard。

### 1.6 Manus — Cloud Browser Take Over

- 触发条件很明确：**遇到 SMS 验证码、CAPTCHA、MFA 这类"人必须亲自在场"的检查**。
- 流程：agent 卡住 → 通知用户 → 用户临时接管浏览器完成验证 → 归还控制 → agent 继续。
- 通知行为可通过设置里的 **"Take Over Notifications"** 配置。
- Browser Operator 用本地浏览器（住宅 IP + 已有登录态），每次会话都要授权，**关掉标签页即停止 agent**；"任务跑偏时点进标签页即可接管"。
- 这是**渠道依赖**思路的典范：HITL 不只是"人做判断"，很多时候是"人必须在那个通道里"。

### 1.7 Cursor — Run Modes 与 Allowlist

三种运行模式（3.6）：

| 模式 | 行为 |
|---|---|
| **Auto-review**（默认） | 白名单内的直接跑；其他 shell 命令尽量进沙箱；剩下的交给 LLM 分类器 |
| **Allowlist** | 只有白名单命令跑；支持的命令可在沙箱中运行 |
| **Run Everything** | 全部不审批 |

- 白名单配置文件：`~/.cursor/permissions.json`（用户级）与 `<workspace>/.cursor/permissions.json`（仓库级），**两者同时存在时数组拼接**；团队管理员配置优先级最高，且会**替换** IDE 里的白名单、让应用内编辑器变为只读。
- 终端白名单用**前缀语义**（`git` 匹配 `git status`，`npm:install*` 匹配 `npm install express`）。
- **三类动作永远弹窗，与模式无关：浏览器动作、文件删除、工作区外的写入。**
- 分类器支持自然语言规则（`allow_instructions` / `block_instructions`）。
- 官方主动声明：**"白名单是 best-effort，不是安全边界。被诱导的 agent 或 prompt injection 可能绕过。"**

### 1.8 对照：Gemini / Perplexity Comet

| 维度 | ChatGPT | Gemini | Perplexity Comet |
|---|---|---|---|
| 同意发生的时机 | 先对话澄清，再交接 | 在目标应用里出草稿卡片，Send 是显式闸门 | agent 运行**之前**按工具授权 |
| 粒度 | clarify-then-handoff | 目标应用的原生草稿卡 | 逐工具权限矩阵（只读 vs 写/删） |
| 产品赌注 | 通用聊天 + 可信交接 | 生态深度（HITL 渲染在 Gmail UI 里） | 靠权限而不是逐条确认建立信任 |

Comet 的首次运行提示提供 **allow once / always allow / deny**，并可按站点屏蔽；Gemini in Chrome 的 Auto Browse 是 opt-in 且被设计为在敏感动作（付款、发帖）前暂停并显式确认。

提炼出的原则：**需要常驻工具权限时"运行前授权"，一次性动作"草稿后授权"**；审批卡必须展示真实 payload（隐藏收件人/diff/金额的审批卡是"表演"）；**确认边界要紧贴在不可逆动作之前**；**信息访问权不等于行动权**；HITL 要和"自主预算"配对——检查点管高影响步骤，预算管无人看护时的推进上限。

---

## 2. 横向对比

| 维度 | ChatGPT/Atlas | Claude Code | Kimi | 豆包 | Grok | Manus | Cursor |
|---|---|---|---|---|---|---|---|
| 审批决策档位 | 检查点 | 一次/会话/永远 | plan 批准/修订 | 强制确认点 | 一次/记忆+硬豁免 | 接管 | 白名单/分类器 |
| 是否有 plan gate | 叙述步骤 | ✅ 且不可绕过 | ✅ 且不可绕过 | ✅ todo 清单 | ✅ 不可绕过 | 无 | ✅ Plan 模式 |
| 记忆化授权 | 自定义指令 | ✅ 持久化设置 | — | — | ✅ 危险模式豁免 | — | ✅ permissions.json |
| 中途结构化提问 | 澄清式 | ✅ AskUserQuestion | ✅ 2–4 选项 | 待确认机制 | — | — | ✅ 2.4 起 |
| 接管/暂停 | ✅ | — | — | ✅ 暂停/接管/终止 | ✅ takeover 全屏 | ✅ Take Over | — |
| 超时策略 | — | 唯一终态含 expired | — | — | — | 通知 | — |
| 审计 | 有限 | ✅ 规则来源/遮蔽检测 | — | 来源标注 | — | — | 团队级配置 |

---

## 3. 十个可借鉴模式（按价值排序）

### P1 — 高价值、低成本

**① 三档决策：本次 / 本会话 / 永久**

AXIS 现在只有 `approved | rejected`（`src/harness/core/models.py:54`、`web/harness-console/src/lib/harness-server.ts:13`、BFF `app/api/harness/approvals/[approvalId]/route.ts`）。改成三档需要：

- `ApprovalStatus` 增补 `APPROVED_FOR_SESSION` / `APPROVED_ALWAYS`，或在 decision 上带 `scope` 字段。
- `ApprovalRequest`（`models.py:189`）增加 `scope` 与 `rule_key`（由 tool_name + 参数指纹派生），用于后续命中判断。
- `src/harness/policy/` 里加一个"会话级临时规则"层，与静态 `PolicyRule` 区分开；会话级规则随会话销毁。
- 关键防坑（来自 Claude Code 的教训）：**危险模式永远豁免记忆化**。AXIS 已经有 `bash_safety.py`，把它的判定结果作为"不可记忆"的闸门。

**② 拒绝要带理由，并回注给模型**

现在审批结果在 AG-UI 里投影为 `{"decision": "rejected"}`（`src/harness/agui/mapper.py:135-144`），模型拿不到原因。应该把 `reason` 一起回传，让 agent 修正而不是重试同一个动作。这是所有参照实现里共识度最高、AXIS 缺失最明显的一条。

**③ 允许并修改参数（approve-with-edits）**

AG-UI 的 interrupt payload 已预留 `editedArgs`。审批卡（`components/approval-card.tsx`）把 `argument_summary.command` 做成可编辑字段，用户改一行命令再放行。对 agent studio 的定位（治理型平台）尤其有价值：**审批人不需要退回给 agent，可以直接修正后放行**。

**④ 从审批反向沉淀策略规则**

用户点了"永远允许"之后，应该能一键变成一条正式 `PolicyRule`（`src/harness/policy/models.py`），并可见、可编辑、可撤销。AXIS 目前**没有任何 policy rule 的 CRUD 路由**（`src/harness/api/routes/` 下不存在），这是治理平台的核心缺口。参考 AgentPatterns.ai 的做法：**挖掘会话记录里高频的只读调用 → 按频次排序 → 提议一份收窄的白名单 → 人工过审**，而不是让人手写规则。

### P2 — 中等成本、结构性收益

**⑤ 引入 plan gate，并且让它不可绕过**

- 后端：`src/harness/runtime/claude_sdk.py:207-224` 的权限模式 resolver 目前只返回 `auto` / `dontAsk`，接入 `plan`。
- 注意 Claude Code 踩过的坑：**plan mode 是可回退的权限状态转换**，进入时保存前一状态，退出时恢复；不要做成独立权限系统。
- 规则上抄 Kimi/Grok：**auto / always-approve 下 plan 审批依然生效**。
- 前端已有 `CodexLoopStage` 的 plan/tools/correction/verification/result 展示（`components/agent-studio/agent-preview.tsx`），但目前是**事后渲染**（`src/harness/studio/try_run.py:25,196-240`），需要变成"事前待批"。

**⑥ 中途结构化提问（elicitation）**

这是 AXIS 完全空白、而参照实现已标准化的能力。两条可选实现路径：

- **AG-UI 原生**：用 `RunFinished { outcome: { type: "interrupt", interrupts: [...] } }` + `RunAgentInput.resume[]`，`reason: "input_required"` 时带 `responseSchema`，前端按 schema 渲染表单。好处是他们的栈本来就是 AG-UI + assistant-ui，语义最正。
- **MCP elicitation 语义**：表单模式只允许**扁平对象的原始类型**（string/number/integer/boolean/enum），不支持嵌套和对象数组；**表单模式严禁用于密码/API key/token/支付凭据，必须走 URL 模式**；单会话同时只允许一个 elicitation 挂起；表单保持 5–7 个字段以内。

抄这个模式时要带上它的安全警告：**澄清模式会放大 prompt injection**——有研究报告 ASR 从 1–11% 升到 24–63%，机制是"来源坍缩"（用户输入进入上下文时被赋予更高信任）。AXIS 已经有 `ContextTrust`（`src/harness/policy/models.py`），把 elicitation 的返回值标记为 `UNTRUSTED` 就能接上现有体系。

**⑦ 审批收件箱 + 路由 + 升级**

- `ApprovalRepository`（`src/harness/core/ports.py:160-177`）目前只有 `get` / `find_by_tool_call` / `compare_and_set` / `list_expired_pending` / `list_for_runs`。加 `list_by_tenant(status=...)`。
- 新增 `GET /approvals`，前端加一个审批收件箱页（`web/harness-console/src/app/` 下）。
- 企业级框架的一致共识：**按风险/领域路由而不是全部倒进一个队列**；**超时默认拒绝**（AXIS 已经是 expire，方向正确）并可配置升级；支持**委派**与交接；每个决策带 **reason code**；不可篡改的审计流水。
- 监控指标参考：审批触发率 < 10%、批准率 > 90%、SLA 达标率 > 95%；**批准率过高本身就是阈值配错的信号**。

**⑧ 暂停 / 接管 / 归还**

AXIS 现在只有 cancel（不可逆）和 free-text steering（`src/harness/runtime/steering.py`，不阻塞、不返回值）。缺 pause/resume 与"人接管"。

- 借鉴 Manus：接管要有**明确的触发条件**（SMS/CAPTCHA/MFA 这类"人必须在场"的步骤）和**可配置的通知**。
- 借鉴 Grok Bot：接管时把操作面全屏交给用户，做完归还；并且**刻意不要默认展开每一步**——一旦露了一个步骤，用户就会要求看完整条 trace。这和 ⑤ 里"plan 要事前批、细节要折叠"是同一个设计张力。

### P3 — 细节打磨

**⑨ 高风险/低风险分流 + 分类器前置**

AXIS 已有基础：`sdk_tool_gate.py:760-778` 对低风险沙箱 Bash 自动放行。方向对，但可以更彻底——**先用确定性规则和分类器过滤，再决定是否打断人**（Claude Code 的"await automated checks before showing dialogs"）。数据支撑：逐次询问的批准率高达 93–97%，说明大部分弹窗没有信息价值。

**⑩ 在预算/轮次到限时给人一个决策点**

`max_turns` / `max_budget_usd` 耗尽现在是直接终态报错（`error_max_turns` / `error_max_budget_usd`，`src/harness/agui/activity.py:326-329,571-587`）。应该改成一次 HITL 询问：是否放宽额度继续这个 run。这是"检查点管高影响步骤、预算管无人看护"原则的自然延伸。

---

## 4. 反模式清单

- **把远端审批当 UI 问题**，而不是带过期、取消、稳定 id 的持久运行时对象。
- **持久化过宽规则**（`Bash(*)`、命令前缀太短）导致权限蔓延。
- **规则遮蔽**：低优先级 allow 被高优先级 ask/deny 挡死，且无人察觉——建议加规则可达性/遮蔽检测的 lint。
- **把"弹窗展示过"当成"已获得有效审批"**。
- **无终态泄漏**：每个提示必须落到 approved / denied / cancelled / expired 之一。
- **过多闸门**：低风险可逆动作也拦，会同时损失产品价值和安全性（人开始机械点击）。有论文的受访者原话就是这个体验的代价。
- **隐藏 payload 的审批卡**：不显示收件人、diff、金额、目标路径的确认窗口是表演。
- **让用户替 agent 调试**：agent 缺乏自知，陷入 try-fail 循环，最后变成人在手动 debug。

---

## 5. 研究证据

**"Why Johnny Can't Use Agents"（CMU，CAIS 2026，N=31，测 OpenAI Operator 与 Manus）**
系统梳理了 102 个商业 agent 的宣传用例，并做出声思维可用性测试。五个障碍：心智模型错位、预设信任、协作僵化、沟通开销、元认知缺失。其中与 HITL 直接相关的是"**协作僵化**"——agent 像"独狼执行工具"，无法适配用户在任务中途的介入需求；有参与者说："这就像我给了你一份工作，你把工作又扔回给我。" 六条设计建议里最相关的是 **"measure twice, cut once"**：把计划和检查点显式化，让用户能在执行前和执行中操舵。另外，很多人**并不理解 Take Over 的作用范围**，这本身说明接管机制需要更好的解释性设计。

**"How Agents Ask for Permission"（UW，arXiv 2607.13718）**
系统调研 21 个权限系统提案并横向分析 5 个主流商业 agent。四个目标：低用户开销、形式化可验证的策略规格、确定性执行、对已授权策略的持续控制。核心发现是**学术与工业各占一半**：前三个目标在学术里很突出、在商业产品里几乎缺席（商业产品要么是高开销的逐条询问，要么是不透明的 LLM 审查）；而"持续的用户控制"（能编辑/撤销已授予的权限）在商业产品里很常见、在学术提案里很少见。**没有任何一个提案同时做到这三点。** 这正是 AXIS 作为治理平台的机会区：把"低开销 + 确定性执行 + 可撤销"三者同时做出来。

**其他可用数据**
- DiscoBench（腾讯混元 + 清华）：11 个模型在需要识别歧义并追问的深度搜索任务上全部低于 50% 端到端准确率；而 "SearchThenAsk" 策略平均 93.4% 成功，对比 "DirectGuess" 56.5%。**在系统提示里加一句"注意歧义"能提高检测率，但不能可靠提升任务完成率**——说明澄清能力需要产品机制而不只是提示词。
- REAL benchmark：前沿模型在 112 个受控多步网页任务上最高只有 41% 成功率。自主性的天花板还很硬，HITL 不是过渡期的妥协。

---

## 6. 落地优先级建议

| 优先级 | 动作 | 主要改动位置 |
|---|---|---|
| P0 | 决策三档化（本次/会话/永久）+ 危险模式硬豁免 | `core/models.py:54`、`policy/`、`approvals.py`、`approval-card.tsx`、BFF route |
| P0 | 拒绝携带理由并回注模型 | `agui/mapper.py:135`、`application/approvals.py`、`approval-card.tsx` |
| P0 | 审批收件箱（`list_by_tenant` + `GET /approvals` + 页面） | `core/ports.py:160`、`api/routes/approvals.py`、`app/` 新页面 |
| P1 | approve-with-edits | `approval-card.tsx`、`agui/mapper.py`、approvals API |
| P1 | 从审批一键沉淀 PolicyRule + 规则 CRUD 与遮蔽 lint | `api/routes/` 新增、`policy/rules.py` |
| P1 | 中途结构化提问（AG-UI interrupt / MCP elicitation 语义） | `agui/routes.py`、`mapper.py`、前端新组件 |
| P2 | plan gate（含可回退的权限状态转换、auto 下不可绕过） | `runtime/claude_sdk.py:207-224`、`studio/try_run.py` |
| P2 | 通知 / 路由 / 升级 / 委派 | `application/approvals.py:229`、`triggers/` |
| P2 | pause / resume / takeover | `runtime/steering.py`、`api/routes/runs.py`、前端 |
| P3 | 预算与轮次到限时的人机决策点 | `agui/activity.py:326-329,571-587` |

一句话总结这次的调研结论：**行业正在从"逐次弹窗"转向"分级自主 + 三个不可绕过的契约点（计划、不可逆动作、人必须在场的通道），并且把审批从对话内的一次性交互升格为可沉淀、可审计、可撤销的策略资产。"** AXIS 在最后一个方向上有天然的产品优势，但三条腿里目前只有"工具调用审批"这一条——而且它还是二档的、不带理由的、没有沉淀出口的。

---

## 参考来源

- [OpenAI Help Center — Using Ask ChatGPT sidebar and ChatGPT Agent on Atlas](https://help.openai.com/)
- [docs.x.ai — Grok Build permissions](https://docs.x.ai/build/features/permissions)
- [docs.x.ai — Grok Bot teams and enterprises](https://docs.x.ai/grok-bot/teams-and-enterprises)
- [ZenML LLMOps Database — Designing persistent multi-agent workflows for Grok Bot](https://www.zenml.io/llmops-database/designing-persistent-multi-agent-workflows-for-grok-bot)
- [Manus 官方文档 — Cloud Browser](https://manus.im/docs/features/cloud-browser)
- [Manus 官方文档 — Browser Operator](https://manus.im/docs/features/browser-operator)
- [Cursor 官方文档 — permissions.json](https://cursor.com/docs/reference/permissions.md)
- [Cursor 官方文档 — Agent Security](https://cursor.com/docs/agent/security.md)
- [AG-UI 协议 — Interrupts](https://docs.ag-ui.com/concepts/interrupts)
- [Vercel AI SDK — Human-in-the-loop cookbook](https://ai-sdk.dev/cookbook)
- [CopilotKit PR #4630 — approval card / batch approval](https://github.com/CopilotKit/CopilotKit/pull/4630)
- [arXiv 2607.13718 — How Agents Ask for Permission: User Permissions for AI Agents](https://arxiv.org/abs/2607.13718)
- [arXiv 2509.14528 — Why Johnny Can't Use Agents](https://arxiv.org/abs/2509.14528) · [项目页](https://cmu-spuds.github.io/why-johnny-can-t-use-agents) · [ACM](https://dl.acm.org/doi/10.1145/3786335.3813140)
- [GitHub — dnnyngyen/kimi-agent-internals](https://github.com/dnnyngyen/kimi-agent-internals)
- [36氪 — 豆包要开始操作你的电脑了](https://eu.36kr.com/zh/p/3948580299848840)
- [OFweek — 深度体验豆包「任务模式」](https://www.ofweek.com/ai/2026-06/ART-201717-8110-30690727.html)
- [guibai.dev — ByteDance Ships Doubao Work](https://guibai.dev/a/7678464532097040426/)
- [做视频网 — 豆包电脑版办公任务模式教程与安全使用指南](https://www.zuoshipin.com/article/23203)
- [agenticcontrolplane.com — The Cursor control model, explained](https://agenticcontrolplane.com/controls/cursor)
- [DEV Community — OpenClaw plugin approvals](https://dev.to/hex_agent/openclaw-2026328-plugin-approvals-grok-web-search-and-acp-channel-binds-i7f)
