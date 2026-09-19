# DeepAgents 运行时的闭环设计与实现

**日期：** 2026-09-19
**分支：** `feature/deepagents-runtime`
**前置结论：** `docs/deepagents-third-runtime-analysis-20260911.md`（当时结论：不接入，只移植 `write_todos` 与压缩）

## 0. 为什么推翻 2026-09-11 的结论

9-11 的分析是在"平台还没有 DeepAgents 产物"的前提下写的：它把 DeepAgents 当作一个**候选能力清单**，
和平台已有能力逐条比对，结论自然是"净新增只有两件事"。

9-14 之后前提变了：平台已经交付了 **DeepAgents 代码视图**（`agent-project-code.tsx`）和
**DeepAgents 项目导出**（`studio/deepagents_export.py` + `GET .../deepagents-project`）。
这两件事把 DeepAgents 从"候选"变成了**已经对用户承诺的产物形态**，于是出现了一个新的、
比"要不要第三个 loop"更硬的问题：

> 控制台给用户看的是 DeepAgents 工程（代码视图），能下载的也是 DeepAgents 工程，
> 但平台跑的是 Claude Agent SDK / Codex。**同一份草稿的"所见"与"所跑"不是同一个东西。**

这就是用户说的"没有闭环"。它不是"少一个 runtime"的功能缺口，而是**产品语义不一致**：
代码视图是**承诺**，不是预览。所以本方案的目标不是"再支持一个框架"，而是
**让代码视图、导出产物、平台运行三者收敛到同一份生成计划上**。

## 1. 现状盘点

### 1.1 已有的两条链路

| 链路 | 入口 | 产物 | 是否在平台内运行 |
| --- | --- | --- | --- |
| 代码视图 | `GET /v1/studio/drafts/{id}/deepagents-project/files` | 只读文本预览（按 revision） | 否 |
| 项目导出 | `GET /v1/studio/drafts/{id}/deepagents-project` | `<name>-<version>-deepagents.zip` | 否（用户自行 `langgraph dev`） |

两者共用 `export_deepagents_project()` 生成同一份 ZIP，`revision` 不一致返回 409。

### 1.2 运行时侧完全不存在 DeepAgents（修改前）

grep 全仓（`src/`），`deepagents` 只出现在 `studio/deepagents_export.py`。运行时契约里没有它：

| 同步点 | 修改前 | 处置 |
| --- | --- | --- |
| `core/models.py` `AgentRuntimeType` | 两个字面量 | ✅ 加 `"deepagents"` |
| `core/manifest.py` `AgentSpec.runtime` | 两个字面量 | ✅ 加 `"deepagents"` |
| `runtime/installed.py` | 二元组 | ✅ 加 `"deepagents"` |
| `studio/catalog.py` `RuntimeCapability` | 两个 | ✅ 加第三个（`preview`） |
| `tests/fixtures/runtime/runtime_capabilities_v0.json` | 两个 | ✅ 加第三个 |
| `composition.py` / `api/dependencies.py` | `zip(INSTALLED_AGENT_RUNTIMES, (claude, codex))` | ✅ 改为显式映射；`zip` 位置耦合本身就是隐患（`strict=True` 加第三个会直接崩） |
| `runtime/registry_codex_runtime.py` 路由器 | 不校验覆盖度 | ✅ 路由器自校验：必须覆盖 `INSTALLED_AGENT_RUNTIMES`，否则拒绝构造 |
| `web/.../runtime-capabilities-contract.spec.ts` | 断言"恰好两个运行时" | ✅ 改为三个 + 新增 DeepAgents 断言 |
| `web/.../lib/agent-studio.ts` `AgentRuntime` | 两值联合 + 归一化兜底 | ✅ 加第三值；**修掉一个真实缺陷**（见 §1.3） |
| `studio/compiler.py` | `runtime_*_unsupported` 门禁 | ✅ **无需改动**：门禁完全由 catalog 的 `capabilities` / `model_api_formats` 驱动 |
| `context/checkpoint.py:66` | 硬编码两个字面量 | ✅ **刻意不改**（见 §4.5） |
| `worker/orchestrator.py:1426,1440,1469` | 三处白名单字面量 | ✅ **刻意不改**（见 §4.5） |
| `studio/agent_builder.py` 任务推荐 | 只推荐 claude/codex | ✅ **刻意不改**：`preview` 运行时不应被自动推荐 |
| `studio/platform_skills.py` | 平台 Skill 包声明兼容两个运行时 | ⚠️ **刻意不改**，改为在 catalog `limitations` 里明说（见 §5.2） |
| `deploy/docker/api.Dockerfile` | `uv export` 不带 extra | ✅ 加 `--extra deepagents`（该镜像同时是 Worker 镜像） |

### 1.3 顺带修掉的三个真实缺陷

1. **173 三个 worker 全崩**：`HARNESS_CUBESANDBOX_API_KEY` 在 `.env.production` 中缺失，
   `SdkCubeSandboxClient.__init__` 直接 `ValueError`。**与本次改动无关**，但它使任何"在 173 跑一个真任务"
   的验证都无法进行，故一并修复（见 §7）。
2. **控制台会把 DeepAgents 草稿静默改写回 Claude**：`agent-studio.ts` 的 `normalizeDraft` 用
   `raw.runtime === "codex-app-server" ? ... : "claude-agent-sdk"` 归一化。运行时选择器是
   **catalog 驱动**的，所以 catalog 一加 DeepAgents，这个兜底就会把用户选的 DeepAgents 在"打开草稿
   → 保存"的一次往返里改成 Claude。已改为按白名单归一化（`normalizeAgentRuntime`）。
3. **`DeferredToolSandboxProvider._may_mutate_workspace` 会吞掉所有 DeepAgents 的文件写入**：
   它原来是 `bash` / `python3 -c <script> write|edit` 白名单，而 DeepAgents 的文件操作全部走
   `bash -lc <command>` 形状，于是 `collect()` 永远不回同步远端工作区。已改为"只读黑名单"：
   只把平台自己的 `python3 -c <script> read|glob|grep` 判为只读，其余一律假定写过了。

## 2. 设计目标与不变量

### 2.1 目标

1. **单一事实来源**：草稿 → 一份纯数据「生成计划」→ 同时产出（a）代码视图文本、（b）导出 ZIP、
   （c）平台内实际运行的图。三者由同一份计划派生，不允许各自推断。
2. **平台仍是唯一控制面**：Run 状态机、审批、策略、配额、事件日志、工作区归档、制品发布
   的所有权不变。DeepAgents 不得引入第二套暂停/恢复或第二份事件流。
3. **能力边界显式**：不支持的能力在**编译期**报错，而不是运行时静默降级。

### 2.2 不变量（硬约束）

| # | 不变量 | 落地方式 |
| --- | --- | --- |
| I1 | `RunStatus` 只能由 Harness 翻转 | **不使用** `interrupt_on`；审批走 `ApprovalService(inline=True)` |
| I2 | Run 的每个对外事实必须是带 `sequence` 的持久 `RunEvent` | 只产出平台既有事件类型，不新增旁路通道 |
| I3 | 工作区必须是平台可归档、可指纹的真实目录 | 文件原语全部代理到平台沙箱，工作区即远端工作区 |
| I4 | 工具能力上限 = Manifest | 图只装配已发布快照解析出的工具；工具门用 `declared_tools` 做上限 |
| I5 | 记忆必须经过同意/保留/敏感级 | **不启用** DeepAgents `memory=[...]`（`AGENTS.md` 文件式记忆） |

## 3. 架构：一份计划，三个消费者

```
AgentDraft ──┐
             ├─► DeepagentsPlan（纯数据，无 IO、无第三方 import）
AgentVersion ┘        │
（发布快照）           ├─► 代码视图：render 成文本（既有 deepagents_export 的渲染器）
                      ├─► 导出 ZIP：render 成可安装工程（既有）
                      └─► 运行时：materialize 成活的 LangGraph 图（新增）
```

`DeepagentsPlan`（`runtime/deepagents_plan.py`）承载且仅承载这些决策：

- `model` / `provider`：`provider:model` 串，由路由 `apiFormat` 决定（anthropic / openai）
- `filesystem_tools`：由 `builtin_tools` 映射出的 FS 工具白名单（**不含 `delete`**）
- `added_export_tools`：`ls` / `read_file` 这类平台目录里没有、但 Skill 渐进披露必须有的工具
- `unmapped_builtin_tools`：声明了但映射不到的（`Task` / `Agent`）
- `has_bash` / `read_only` / `permissions`：权限档位，以及"只读是否真能拦"
- `shell_timeout`：由 `limits.timeoutSeconds` 截断
- `recursion_limit`：由 `limits.maxTurns` 近似（每轮两步）
- `with_mcp` / `with_skills`

**关键收益**：代码视图里看到的 `agent.py` 与平台实际运行的图，参数来自同一个 dataclass。

## 4. 运行时设计

### 4.1 模块划分

| 文件 | 职责 |
| --- | --- |
| `runtime/deepagents_plan.py` | 纯数据计划（无第三方 import、无 IO），三个消费者共用 |
| `runtime/deepagents_backend.py` | `HarnessSandboxBackend(BaseSandbox)`：把 FS 原语代理到平台沙箱 |
| `runtime/deepagents_tool_gate.py` | `DeepagentsToolGate(AgentMiddleware)`：策略 + 审批 + 配额 |
| `runtime/deepagents_events.py` | LangGraph 流 → 平台 `RuntimeEvent` 映射（纯函数，无 LangChain import） |
| `runtime/deepagents_runtime.py` | `DeepagentsRuntime`：装配图、驱动 `astream`、产出事件 |
| `runtime/registry_deepagents_runtime.py` | 从不可变发布快照解析 route / 工具 / MCP / Bundle 算子后委派 |
| `runtime/deepagents_factory.py` | 唯一一处**惰性 import**，缺 extra 时给可读错误 |
| `runtime/file_capabilities.py` | 与运行时无关的 `RunFileCapabilities`（从 Claude 工具门提取，两边共用） |
| `runtime/approval_review.py` | 与运行时无关的审批摘要/风险等级（同上） |

### 4.2 工作区与执行后端

**DeepAgents 运行时要求存在沙箱命令执行器**（`context.sandbox_command_executor`），
没有则在 `execute` 开头抛 `ConflictError`。理由：DeepAgents 的文件语义必须落在
**平台可归档、可指纹**的工作区上（I3），而 Worker 进程内没有这样的工作区。

`HarnessSandboxBackend` 继承 0.7.13 的 `BaseSandbox`，只实现抽象集
`{execute, upload_files, download_files, id}`，`ls/read/glob/grep/edit/write`
由基类基于 `execute()` 组合。因此**只依赖 `RuntimeContext.sandbox_command_executor` 这一个既有抽象**，
不需要给沙箱 provider 增加文件传输 API。

两条**承重契约**（写在模块 docstring 里）：

1. **命令形状**：上传/下载走 `python3 -c <script> <operation> <base64 payload>`，
   刻意与平台自己的远端文件工具同形，`DeferredToolSandboxProvider` 才能分类出只读命令。
2. **`glob` 的根**：`BaseSandbox._glob_search_root` 会把相对路径绝对化成 `/{root}`，
   所以 `aglob` 必须先向沙箱问一次工作目录（缓存），传绝对根，再把结果前缀剥回去；
   否则一次 `ls(".")` 会遍历整个文件系统。
3. **GNU grep**：`BaseSandbox` 的 grep 解析 `path\0line:text`，需要 `-Z`（GNU 专有）。
   平台沙箱镜像是 Linux，满足；macOS 本地跑集成脚本时不满足，属环境差异。

### 4.3 工具门（I1/I2/I4 的落地）

`DeepagentsToolGate.awrap_tool_call` 逐次调用（`AgentMiddleware` 的 `awrap_tool_call` 是
Claude SDK `PreToolUse` 钩子的对位点）：

1. **先把工具名翻译回平台词汇**：`execute→Bash`、`write_file→Write`、`ls→Glob`……
   策略规则、配额资源、写越界检查全部写在平台 builtin 名下，不翻译则规则永不命中；
2. `Write`/`Edit` 先做工作区越界检查，越界直接拒绝；
3. `PolicyEngine.evaluate()` → `ALLOW` / `ASK` / `DENY`，并复用平台三条窄放行规则
   （沙箱内低危 Bash、已发布 MCP 工具、已声明 Bundle 算子）；
4. `DENY` → 追加 `tool.result`（`error.code = "policy_denied"`）并返回错误 `ToolMessage`（模型可自纠）；
5. `ASK` → `ApprovalService.request(inline=True)` + `wait_for_decision()`；
   **Run 状态仍由 Harness 翻转**，取消/超时语义与 Claude 路径一致；
6. 配额消费：`mcp__*` 计 `MCP_REQUESTS`，与 Claude 路径同键同幂等。

**`interrupt_on` 全程不启用**——它会产生 LangGraph 的 `__interrupt__` 第二套挂起机制，
与 `RunStatus.WAITING_APPROVAL` 冲突。

**`tool.request` 只由工具门写一次**：`DeepagentsRuntime` 不把 `tool.request` 透传给 Worker，
与 `ClaudeSdkRuntime` 在存在工具门时过滤 `tool.request` 的行为完全对称，
从而保证一个 Run 只有一套审批机制、不重复评估策略。

### 4.4 事件映射

`astream(..., stream_mode=["messages","updates"])`：

| LangGraph | 平台事件 |
| --- | --- |
| 首个可见文本 chunk | `message.start` |
| 文本 delta | `message.delta` |
| 推理块（`reasoning_content` / thinking block） | `reasoning.delta` |
| `AIMessage.tool_calls` | `tool.request` |
| `ToolMessage` | `tool.result` |
| 子图（`checkpoint_ns` 含 `\|`）文本 | `subagent.delta` |
| 有可见文本的模型轮结束 | `message.completed` |
| 收尾 | `runtime.result`（`num_turns`、`usage`、`stop_reason`、`duration_ms`） |

外加开头的 `model.route.selected`。

**不产出 `runtime.thread.started` / `runtime.session.recovered`**（见 §4.5）。

### 4.5 会话与恢复：刻意不绑 runtime thread

第一版设计（本文件早期版本）打算给图挂 `AsyncSqliteSaver` 并把 `thread_id` 绑到
`session.resolved_runtime_thread_id`。**实现时放弃了这条路**，原因是一条承重不变量：

> Worker 看到 `runtime.thread.started` 就会 `bind_runtime_thread`，把
> `resolved_runtime_thread_id` 落库；而 `context/checkpoint.py` 一旦看到它非空，
> 就**关掉会话历史的 durable replay**。

也就是说：绑 thread = 把会话连续性的权威从平台交给图状态。而 DeepAgents v1 没有 checkpointer，
图状态本来就不可靠。所以正确选择是**根本不绑**：

- 运行时**不产出**任何 thread 绑定事件 → `resolved_runtime_thread_id` 保持为空
  → 平台继续把该会话的持久历史投影进每个 Run；
- 因此 `context/checkpoint.py:66` 的 `{"claude-agent-sdk", "codex-app-server"}` 白名单
  **不需要也不应该**加 `deepagents`：那个白名单的语义是"拥有 runtime thread 的运行时"；
- `worker/orchestrator.py` 的三处白名单同理，保持不动才是**承重**的：
  它们能挡住未来某次改动不小心让 DeepAgents 发出 thread 事件；
- `pyproject.toml` 因此**不装** `langgraph-checkpoint-sqlite`（已从 extra 中移除）。

### 4.6 模型

模型在 worker 进程内构造，凭据来自控制面 `ModelConfigurationService.resolve_runtime()`，
**不经过环境变量、不进子进程**（这是相对 Codex 的净收益）：

| 路由 `apiFormat` | LangChain 类 |
| --- | --- |
| `anthropic_compatible` | `ChatAnthropic` |
| `openai_compatible` | `ChatOpenAI` |

`resolve_runtime` 原来把 `route.api_format` 丢掉了，只留 `provider`（那是**路由身份/鉴权**，
不是协议）。本次给 `CcSwitchClaudeConfig` 补了 `api_format` 字段——它本来就是唯一的协议判据
（`_runtime_sdk_base_url` 已在用它裁 `/v1`），且是新增可选字段，零额外控制面往返。

DeepAgents 是**唯一同时支持两种文本协议**的运行时，所以 `resolve_runtime` 不传
`required_api_format`；不支持的协议由编译器在发布前拦下。

## 5. 能力契约（能力清单，不吹）

### 5.1 catalog 条目（实现后的真实值）

```json
{
  "runtime": "deepagents",
  "label": "DeepAgents",
  "stability": "preview",
  "capabilities": ["skills", "builtin_tools", "python_tools", "mcp_http",
                   "session_resume", "approvals", "artifacts", "planning"],
  "modelApiFormats": ["anthropic_compatible", "openai_compatible"],
  "limitations": [
    "Studio Sub Agents are not connected",
    "Knowledge references are not connected",
    "On-demand tool search is not connected",
    "Only streamable HTTP MCP registrations are supported",
    "Platform memory bank is not connected",
    "File-based AGENTS.md memory is never enabled",
    "Platform catalog Skills are not reviewed for this runtime; embed the Skill"
  ]
}
```

与早期设计稿的差异（**以本节为准**）：

- **`subagents` 不在能力清单里**。委派子智能体需要按子快照逐个解析工具/策略/配额，
  v1 没接通，所以运行时用 `_NoSubagentsMiddleware` **占用 `SubAgentMiddleware` 槽位**，
  避免 DeepAgents 装上一个平台从未解析、从未计数、从未治理的默认子智能体；
  编译器同时以 `runtime_subagents_unsupported` 在发布前报错。
- **`planning`** 对应 `TodoListMiddleware`，是 9-11 分析里唯一认定的净新增能力。
  编译器不额外为它开门禁——`capabilities` 里没有 `planning` 的运行时会被通用
  `runtime_<feature>_unsupported` 门禁拦下，DeepAgents 有，所以放行。

明确**不支持并在编译期报错**：Sub Agent、知识库、按需工具加载、MCP `sse`/`stdio`、
WebSearch/WebFetch 内置联网、`memory` 文件式记忆。

### 5.2 平台 Skill 包（已由实测放开）

平台自带的目录 Skill 包（`platform_skills.py`）原先只声明
`compatibleRuntimes=("claude-agent-sdk", "codex-app-server")`。当时的判断是：运行时的
Skill 通路已经实现，但"某个具体 vendored Skill 在 DeepAgents 上真的跑得通"是**产品声明**，
不能在没验证过的情况下加，所以先在 catalog `limitations` 里明说这条边界。

**现在已放开，依据是测量而不是推断：**

- **参考路径（产品路径）**：`spec.skillReferences=["internal-comms"]` → 校验
  `ready=True, issues=0`、不再出现 `skill_reference_runtime_incompatible`、manifest 列出
  `skills/internal-comms`；Run 内 `agent.assets.staged {"skills": ["internal-comms"]}`，
  内核读了 `SKILL.md` **和** `references/examples/3p-updates.md`（即真的按 Skill 的引用走），
  写出 `outputs/weekly-3p.md` 并触发 `artifact.ready`。**13/13 全通过。**
- **直挂路径**：`minimax-docx` 经 `/drafts/{id}/skills/catalog/{package}/install` 挂载，
  内核读 `SKILL.md`、跑 `scripts/env_check.sh`、产出文档。**9/9 全通过。**

这条 `compatibleRuntimes` 门在**编译期**生效（`compiler.py:113` 静默跳过、
`compiler.py:629` 报 ERROR），所以在放开之前，一个声明了引用的 DeepAgents 草稿会出现
**代码视图里有 Skill、实际运行拿不到**的分裂——正是本项目最不该有的那种不一致。

**顺带记录一条与运行时无关的发现：** `minimax-docx` 走参考路径时 Run **超时**了。
原因不是运行时，而是该 Skill 自带 `env_check.sh` 判定 **.NET SDK 为必需项**，而平台当前
Sandbox 模板没预装，于是模型照 Skill 的指引去 `wget dotnet-install.sh` + `pip3 install
python-docx`，把预算烧光。这是"Skill × 沙箱模板"的前置条件问题，**Claude / Codex 同样会踩**。
处置：在 `_VENDORED_PREREQUISITES` 里把这个前置条件写进该包的 `findings`，
让控制台如实告诉用户，而不是让用户看到一个莫名超时的 Run。

## 6. 依赖与镜像

`deepagents==0.7.13`（`langgraph 1.2.11` / `langchain 1.4.1` / `langchain-core 1.6.3`）
**不进** `[project] dependencies`，而是 `[project.optional-dependencies].deepagents`：

- extra 内含 `deepagents==0.7.13`、`langchain-openai`（0.7.13 不自带）、
  `langchain-mcp-adapters`（与导出渲染器同区间）；
- `uv.lock` 同步（163 包）；
- `deploy/docker/api.Dockerfile` 用 `uv export --extra deepagents` 安装——
  **该镜像同时是 Worker 镜像，而 Worker 才是真正执行 Run 的进程**；
- **只有 `runtime/deepagents_factory.py` 做惰性 import**。`runtime/deepagents_plan.py`
  与 `runtime/deepagents_events.py` 完全不 import 第三方，所以它们的测试在任何环境都能跑；
- `HARNESS_RUNTIME=multi` 缺 extra 时在**组合期**抛可读错误，而不是等到第一个 Run 才 ImportError。

这样"平台主体不依赖 LangGraph"这条 9-11 的原则仍然成立：不装该组，
`runtime=fake` / `runtime=claude-sdk` 照常运行（`Settings.runtime` 默认就是 `fake`），
只是 `multi` 不可用（与缺 `codex` CLI 时 Codex 不可用同一语义）。

### 6.1 增量镜像里唯一一处版本变更：`wcmatch`

增量镜像复用现网 `develop-bfa9f8a` 作为 base，extra 的 38 个包全部是新增，
**唯一会动的既有包是 `wcmatch`：base 装的是 `10.2.1`，extra 拉进来的是 `11.0.1`。**

冲突是 pip 报出来的：

```
e2b 2.33.0 requires wcmatch<11,>=10.1, but you have wcmatch 11.0.1 which is incompatible.
```

查清后结论是**名义冲突，不阻塞**：

- `deepagents 0.7.13` 的元数据写死 `wcmatch>=11.0`，**没有 11 以下的可能**；
- base 里的 `e2b 2.33.0` 要求 `wcmatch>=10.1,<11`；
- 而 `uv.lock`（项目自己的事实来源）解析结果是 `e2b==2.51.0` + `wcmatch==11.0.1`——
  也就是说 **base 镜像相对自己的 lock 是陈旧的**（`e2b 2.33.0` vs lock 的 `2.51.0`），
  上游早已把 e2b 升到允许 wcmatch 11 的版本；
- 全仓只有 `e2b/template/utils.py` 用到 `from wcmatch import glob`，
  该模块只服务于 **模板构建**（`e2b.template.*` / `e2b.template_async.*`）。
  平台侧 `sandbox/e2b.py`、`sandbox/cubesandbox.py` 只 import
  `AsyncSandbox` / `ConnectionConfig` / `SandboxOpts` / `ENVD_*`，
  **从不引用 `e2b.template`，也从不自行构建模板**（模板由外部预构建，平台按 id 引用，
  例如 `tpl-f116a5f3d1c442b2b1690f4d`）。

因此这里**刻意不在 extra 里钉 `e2b`**：把 e2b 从 `2.33.0` 升到 `2.51.0` 会让本改动的
影响面从"DeepAgents 运行时"外溢到 Claude / Codex 的沙箱通路，而收益为零
（唯一受影响的代码路径平台根本不执行）。正确的修法是把 base 镜像对齐到自己的 `uv.lock`，
那属于基础镜像的事，不属于 DeepAgents overlay。

## 7. 173 验证方案

1. 修复 `HARNESS_CUBESANDBOX_API_KEY`（补值 + 重建 api/worker），确认三 worker healthy —— **已完成**；
2. 在 173 上以现网镜像为 base 构建增量镜像（Harbor 与清华 PyPI 镜像在 173 均可达）——
   **已完成**（`kai/axis-api:deepagents-20260919.2`，api + 3 worker 全 healthy）；
3. 走完整闭环：建草稿（runtime=deepagents）→ 校验 → 代码视图 → 导出 ZIP → 发布 →
   新建任务运行 → 观察 `model.route.selected` / `tool.request` / `artifact.ready` / `runtime.result`；
4. 记录回滚点与镜像 digest —— 回滚即从 `-f` 链里去掉 `compose.deepagents-20260919.yaml`。

### 7.1 闭环在第 7 步暴露的第一个真实缺陷（已修）

`/v1/runs/{id}/events` 走到 `run.queued → run.provisioning → sandbox.provisioned →
run.running → run.failed`，`model.route.selected` 从未出现。Worker 日志给出根因：

```
ERROR:harness.worker.orchestrator:run execution failed
  error_type=ValueError message=configure exactly one policy engine or profile registry
```

`DeepagentsToolGate.__init__` 强制 `(policy is None) == (profiles is None)` 即报错，
而 `DeepagentsRuntime` 既不接收也不转发策略来源，于是门在**第一次工具调用**就炸——
每一次 DeepAgents Run 都必然失败。

根因不在门，而在**接线**：`composition.py` / `api/dependencies.py` 构造
`build_deepagents_runtime(...)` 时没有传策略来源（Claude 通路是
`SdkToolGate(profiles=policy_profiles, ...)`，一直是对的）。

修法与防线：

- `DeepagentsRuntime` / `RegistryDeepagentsRuntime` 接收 `policy` / `policy_profiles` 并转发给门；
- `build_deepagents_runtime` 在**组合期**校验"恰好一个策略来源"，
  使同类漏接在**启动时**炸掉，而不是等 Sandbox 都 provision 完才在第一个 Run 上炸；
- 传的是**注册表**而非某个已解析的 engine：门的策略以**该 Run 自己的快照**为准，
  只有注册表能解析任意 policy id；
- 新增 `tests/unit/runtime/test_deepagents_tool_gate.py`（12 例）。
  此前这个门**没有任何直接测试**——它是模型与工具之间唯一的授权点，
  也是这次缺陷漏到线上的直接原因。

### 7.2 闭环跑通后暴露的第二个真实缺陷：`tool.request` 被写了两遍

修好接线后闭环即成功（`status=succeeded`，17 种事件）。但事件流里每次工具调用都有
**两条 `tool.request`**：

| 序号 | 写入方 | `name` | 参数 |
| --- | --- | --- | --- |
| 162 | `DeepagentsToolGate` | `Bash`（平台词汇） | `{"command":"pwd"}`，已脱敏 |
| 195 | Worker 通用策略通道 | `execute`（运行时词汇） | 原文 |

第二条还跟了一条 `tool.result`，写着 `policy_denied / implicit-deny`——**而工具其实执行成功了**。

根因是**一份事实被两个 loop 解释**（正是 9-11 记录过的 Codex 那类摩擦）：
`DeepagentsStreamMapper` 会产出 `tool.request`，`DeepagentsRuntime` 只拿它计数、
**没有从产出流里剔除**，于是 Worker 的通用策略通道又处理了一遍。对比
`ClaudeSdkRuntime`（`claude_sdk.py:1455`）——它明确 `if self._tool_gate is not None`
就把 `tool.request` 全部滤掉。门的文档字符串也早已声明"the runtime drops
`tool.request` from the events it yields"，只是**实现没跟上声明**。

三处后果，都从这一条重复事件来：

1. **重复事件**：审计流里每次调用两条；
2. **脱敏被绕过**：Worker 用 `redact_tool_arguments(name, ...)` 脱敏，而脱敏表
   `_CONTENT_FIELDS_BY_TOOL` 按**平台名**（`Write`）索引；运行时名是 `write_file`，
   查不到条目，于是 `content` 明文落库。173 上可见
   `"content": "[REDACTED]"`（门）与 `"content": "deepagents loop ok"`（Worker）并存——
   对携带密钥的参数就是一次真实泄漏；
3. **假的拒绝**：Worker 拿运行时名求策略，`execute`/`write_file`/`read_file`
   不匹配任何平台规则，必然 `implicit-deny`，于是留下一条与事实相反的 `tool.result`。
   更严重的是 `ASK` 分支：`orchestrator.py:1676` 会**挂起 Run 等审批**——
   即"第二套暂停恢复机制"，正是 I2 明令禁止的。

修法（`deepagents_runtime.py`）：**计数之后 `continue`，不再产出**。
计数放在丢弃之前，所以 Manifest 的 `maxToolCalls` 上限不受影响
（`tests/unit/runtime/test_deepagents_runtime.py` 专门钉住这一点，
并用一条非空洞性断言证明映射器确实会产出该事件）。

### 7.3 `artifact.ready`：先误判为"能力缺口"，实测后纠正

闭环第一次跑完时 17 项检查里唯一未通过的是 `artifact.ready`，`artifacts=0`。
当时的判断是"DeepAgents 没有制品通路"，**这个判断是错的**，需要在此更正并留下过程。

**错在哪：** 只去找了"模型能调用的发布工具"。`publish_artifact` 确实是
`claude_sdk.py:975-978` 用 `create_artifact_mcp_server()`
（`artifact_tools.py:224`，基于 `claude_agent_sdk.create_sdk_mcp_server`）注册的
**进程内 MCP 工具**，`harness-artifacts` 也确实不会进 DeepAgents 的图
（`registry_deepagents_runtime.py` 只把 streamable HTTP 注册变成连接，
`sdk` 传输限定为 Studio Bundle 算子）。到此为止都是对的。

**漏掉的是 Worker 里那条与运行时无关的通路**：`orchestrator._publish_workspace_outputs`
在 Run 结束时收集两类候选（`orchestrator.py:487-512`）：

1. **`<workspace>/outputs/**`** 下的全部文件（排除符号链接）——这是 Run 交付产物的
   正式契约，任何运行时都走这条路；
2. **最终回复里点名的文件**，由 `final_artifact_paths` 用
   `_MARKDOWN_ARTIFACT_PATH` / `_PLAIN_ARTIFACT_PATH` 解析，排除
   `.claude` / `.git` / `.harness-runtime` / `.tmp` / `inputs` 这些非交付根。

两条都已发布的路径会被跳过（按 `source_path` 去重），所以不会重复发布。

**为什么第一次没触发：** 提示词让模型"在工作区创建 `hello.txt`"。文件落在工作区**根目录**，
不在 `outputs/` 下（规则 1 不命中）；而 `_PLAIN_ARTIFACT_PATH` 的正则要求路径
**至少含一段目录**（`(?:\.?/?[A-Za-z0-9_.-]+/)+`），裸文件名 `hello.txt` 不匹配
（规则 2 也不命中）。所以那次 `artifacts=0` 是**规则的正确结果，不是能力缺失**。

**实测证据：** 用平台 Skill `minimax-docx` 跑一次（它的 SKILL.md 指导模型写入
`outputs/`），Run 成功后事件流里出现 `artifact.ready`，载荷
`{"name": "weekly.docx", "media_type": "application/octet-stream", ...}`，
`/v1/runs/{id}/artifacts` 可见该文件。闭环脚本据此改为让模型写 `outputs/`，
并把 `artifact.ready` 与"产出了被要求的文件"两条恢复为**断言**。

**结论：** DeepAgents 的制品能力与其它运行时**完全一致**——走的是平台级的
workspace 收集。catalog 把 `artifacts` 作为平台级能力统一声明，是准确的。
唯一仍然只有 Claude 拥有的是那个**进程内 `publish_artifact` 工具**（模型可以
在 Run 中途主动发布），而 Codex 同样没有。因此这**不是**缺口，也**不需要**为
DeepAgents 补一个 `mcp__harness-artifacts__publish_artifact` 工具。

**教训：** 判断"某运行时是否支持某能力"，要去读**平台侧的收集/结算逻辑**，
不能只看"这个运行时能不能调到某个工具"。前者才是能力的实际来源。

### 7.4 模型用绝对路径写文件时，产物落到了工作区里的"嵌套副本"（已修）

7.3 纠正后闭环脚本改成让模型写 `outputs/hello.txt`，脚本仍报 `artifact.ready` 缺失、
`artifacts=0`，而工具调用里**明明写成功了**：

```
TOOL {"name": "Write", "arguments": {"file_path": "/home/user/harness/run_956c.../outputs/hello.txt",
                                     "content": "[REDACTED]"}}
--- 最终回复 ---
已在工作区 `outputs/` 目录下创建 `hello.txt`
```

模型写的是**沙箱里的绝对路径**。Worker 日志给出了决定性证据——整条 Run 只对沙箱文件
API 发过一次请求，路径被**拼接了两遍**：

```
GET http://172.20.109.111/files?path=%2Fhome%2Fuser%2Fharness%2Frun_956c...%2F
                                        home%2Fuser%2Fharness%2Frun_956c...%2Foutputs%2Fhello.txt
```

**根因：** `HarnessSandboxBackend.workspace_relative()`
（`runtime/deepagents_backend.py`）把**任何**绝对路径当作 DeepAgents 的*虚拟*路径，
剥掉前导 `/` 后当作工作区相对路径使用。于是
`/home/user/harness/<run_id>/outputs/hello.txt` 被读作
`home/user/harness/<run_id>/outputs/hello.txt`，而命令的工作目录已经是
`/home/user/harness/<run_id>`，最终写到
`<workspace>/home/user/harness/<run_id>/outputs/hello.txt`。
`collect` 把这个嵌套目录整棵同步回来，`_publish_workspace_outputs` 只看
`<workspace>/outputs/`，自然是空的。

**为什么只有 DeepAgents 中招：**

- 模型得知这个绝对路径的成本是**零**：平台自己的 `Bash` 工具跑一句 `pwd` 就返回了
  沙箱真实工作区路径（本次事件流第一步就是 `Bash {"command": "pwd"}`）。而
  `VISIBLE_EXECUTION_CONTRACT` 只禁止把绝对路径*当作交付物展示*，并没有禁止使用它。
- `BaseSandbox` 的契约本来就是**绝对路径**：`read`/`write` 的入参文档写的是
  "Absolute path"，`glob` 的返回值也是绝对路径（`_absolutize_glob_path`），
  `_glob_search_root(None)` 返回 `"/"`。也就是说，**偏离契约的是这个后端，不是模型**。
- 平台自己那套远程文件工具（`runtime/sandbox_tools.py` 的 `_REMOTE_TOOL_SCRIPT`）
  处理得是对的：`(root / candidate).resolve()` 在 `candidate` 为绝对路径时按
  pathlib 语义**丢弃左侧**，所以绝对路径解析正确、且仍受工作区包含性检查。
- 门（`DeepagentsToolGate`）用的是运行时中立的 `RunFileCapabilities._normalize`，
  它**已经**会识别 `context.remote_workspace` 与 `/workspace` 别名，把该路径正确判为
  `outputs/hello.txt` 并放行。**门放行的名字与后端实际写入的位置不一致**——
  这是一次"审计说创建了 A，字节写到了 B"的完整性缺口，比单纯少一个产物更严重。

**修法（保持最小、并让后端与门对齐）：**

1. `workspace_relative(value, *, root=...)` 新增根剥离：绝对路径若落在沙箱工作区根
   之下，先剥掉该根；`/workspace` 作为平台级别名同样剥离（`RunFileCapabilities`
   与 Bash 安全审查都这么读它）。
2. `HarnessSandboxBackend` 新增 `_relative()`：只有当路径是绝对路径时才去要工作区根，
   而根**优先取 `context.remote_workspace`**——那正是门用来解析模型路径的同一个值，
   于是后端与门**在构造上**不可能分歧；取不到时回退到 `pwd`（`local` 提供者就是这种情况），
   而 `pwd` 返回的正是模型会看到、会复用的那个字符串。相对路径不产生任何额外往返。
3. 其余绝对路径仍按虚拟路径处理，因此 `FilesystemMiddleware` 传下来的 `/`、`/outputs`
   语义不变，且虚拟路径**永远出不去工作区**（前导 `/` 是被丢弃而不是被解析）。

**验证（这次是正面复现，不是碰运气）：** 闭环脚本改为跑两个场景，差别只在提示词如何拼路径——
相对拼法，以及让模型先 `pwd` 再用绝对路径。实测 `run_762c2e9f3d4c4b55b612f254a130bfbf`
的工具参数与失败那次**完全同形**：

```
tool.request name=Bash  args={"command": "pwd"}
tool.request name=Write args={"file_path": "/home/user/harness/run_762c.../outputs/abs.txt", ...}
tool.request name=Read  args={"file_path": "/home/user/harness/run_762c.../outputs/abs.txt"}
artifact.ready {"name": "abs.txt", "source_path": "outputs/abs.txt", ...}
```

`RESULT: 33/33 checks passed`，两个场景 `status=succeeded`，`artifacts=1`，
且 `source_path` 恰为 `outputs/<name>`（新加的断言：产物必须来自工作区相对源，
出现重复目录段即视为"被重新定根"）。

**教训：** 当平台把一个"命名空间"（这里是沙箱绝对路径）通过自己的工具交给模型，
后端就必须能解析这个命名空间；只要后端和策略门对同一个字符串有两种理解，
就一定会出现"门放行 A、实际写 B"的缺口。修法不是让模型别用绝对路径，
而是让两处**共用同一个根**。

## 8. 风险

| 风险 | 处置 |
| --- | --- |
| LangGraph 生态版本漂移快 | 钉死 `deepagents==0.7.13`；升级需重跑导出契约 + 运行时契约测试 |
| 沙箱后端每文件操作一次往返 | 只读场景可接受；`BaseSandbox.enable_capture_offload` 默认关闭，保持行为可预测 |
| 运行时与导出对 Bundle 算子的**工具名拼写**不同 | 平台侧用 `mcp__harness-python-<agent>__<tool>`（策略/配额/工具目录的词汇），导出项目用裸名（独立工程没有平台策略引擎）。算子、schema、沙箱执行语义完全一致，差异只在模型可见的标识符 |
| 第三个 runtime 再次放大"同一份 manifest 被两个 loop 解释"的摩擦 | 计划单一来源 + 编译期能力门禁，避免 Codex 当年的静默丢失 |
| macOS 本地无法端到端跑 grep 通路 | `BaseSandbox` 的 grep 需要 GNU `-Z`；本地验证脚本容错，真实验证在 173 Linux 沙箱 |

## 9. 实现落点

**新增**

```
src/harness/runtime/deepagents_plan.py
src/harness/runtime/deepagents_backend.py
src/harness/runtime/deepagents_events.py
src/harness/runtime/deepagents_tool_gate.py
src/harness/runtime/deepagents_runtime.py
src/harness/runtime/registry_deepagents_runtime.py
src/harness/runtime/deepagents_factory.py
src/harness/runtime/file_capabilities.py      # 从 sdk_tool_gate.py 提取
src/harness/runtime/approval_review.py        # 从 sdk_tool_gate.py 提取
```

**修改**

```
src/harness/core/models.py                    AgentRuntimeType
src/harness/core/manifest.py                  AgentSpec.runtime
src/harness/runtime/installed.py              INSTALLED_AGENT_RUNTIMES
src/harness/studio/catalog.py                 第三个 RuntimeCapability
src/harness/studio/deepagents_export.py       改读共享计划（不再自行推导）
src/harness/runtime/cc_switch.py              CcSwitchClaudeConfig.api_format
src/harness/studio/model_configuration.py     回填 api_format
src/harness/runtime/sandbox_tools.py          提取 run_bundle_python_tool（两个运行时共用）
src/harness/runtime/sdk_tool_gate.py          改用提取出的共享件
src/harness/sandbox/deferred.py               _may_mutate_workspace 改为只读黑名单
src/harness/runtime/registry_codex_runtime.py 路由器自校验覆盖度
src/harness/composition.py                    显式映射 + 工厂
src/harness/api/dependencies.py               同上
pyproject.toml / uv.lock                      deepagents extra
deploy/docker/api.Dockerfile                  uv export --extra deepagents
web/harness-console/src/lib/agent-studio.ts   AgentRuntime 三值 + 归一化修正
web/harness-console/tests/runtime-capabilities-contract.spec.ts
tests/fixtures/runtime/runtime_capabilities_v0.json
tests/unit/api/test_runtime_composition.py
tests/contract/test_runtime_capabilities_contract.py
```

**新增测试**

```
tests/unit/runtime/test_deepagents_plan.py               9
tests/unit/runtime/test_deepagents_events.py            11
tests/unit/runtime/test_registry_deepagents_runtime.py   9（deepagents 缺失时 skip）
tests/unit/runtime/test_deepagents_tool_gate.py         12（7.1 的回归：门此前零直测）
tests/unit/runtime/test_deepagents_runtime.py            3（7.2 的回归：withhold 且仍计数）
tests/unit/runtime/test_deepagents_backend.py           24（7.4 的回归：两种拼法解析到同一文件）
tests/contract/test_runtime_capabilities_contract.py    +2（DeepAgents 协议/门禁）
```
