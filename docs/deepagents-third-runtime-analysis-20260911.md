# 第二个 runtime 之后：是否再接入 DeepAgents

**日期：** 2026-09-11

**状态：** 分析结论（未形成实施决策）

**范围：** 评估在现有 `claude-agent-sdk` 与 `codex-app-server` 两个 runtime 之外，再接入 DeepAgents 作为第三个 runtime 的收益、成本与前置条件。

## 结论

不建议把 DeepAgents 作为第三个 runtime 接入。建议把 DeepAgents 当作"能力清单"而不是"运行时"来读：它真正对本平台净新增的只有**任务规划（`write_todos`）**和**上下文压缩与 prompt caching**两件事，其余能力平台都已具备且约束更强。这两件事都可以在当前 loop 内实现，不需要引入 LangGraph 全家桶。

这个结论与 `docs/plans/2026-07-13-production-capabilities-design.md:19,32` 的既有判断一致，但理由需要更新：当初的理由是"会重复 session、checkpoint、approval、tool、trace、filesystem 语义"。在 Codex runtime 落地之后，这条理由已经被实测过一遍，现在可以用具体数字和具体缺口来陈述，而不是停在抽象层面。

## 起点修正：这里已经是双 runtime

`docs/plans/2026-07-13-production-capabilities-design.md:19` 写的是"deliberately keeps one agent loop"，`docs/agent-production-platform-design.md:81` 与 `README.md:18` 也复述了"不用 LangGraph 重写 Claude Agent SDK 的执行语义"。但代码现状不是这样：

- `src/harness/runtime/installed.py:11-14` — `INSTALLED_AGENT_RUNTIMES = ("claude-agent-sdk", "codex-app-server")`。
- `src/harness/core/models.py:9` — `AgentRuntimeType = Literal["claude-agent-sdk", "codex-app-server"]`。
- `src/harness/runtime/registry_codex_runtime.py:291-319` — `RegistryRuntimeRouter` 按 `snapshot.manifest.spec.runtime` 分发。
- `src/harness/runtime/codex_runtime.py` 等五个模块共约 2,125 行，`claude_agent_sdk` 的 import 数为 0。

所以真实起点是 **2 → 3**，不是 1 → 2。Codex 这一次扩容提供了完整的成本样本，这是本次分析的主要依据。

## 成本模型

接入成本分三层，只有第一层是可见的。

### 第一层：适配器（可写，Codex 已实测）

runtime 契约本身很薄，`src/harness/runtime/base.py:102-110` 只有 `RuntimeEvent(type, payload)` 和 `AgentRuntime.execute(context) -> AsyncIterator[RuntimeEvent]`。所以"实现一个 runtime"看起来便宜，Codex 的实测数字是：

| 项目 | 数字 |
| --- | --- |
| 首个提交（`f044877` / `685264c`，2026-08-22） | 33 个文件，+2,266 / −59 |
| 五个 Codex runtime 模块 | 约 2,125 行 |
| Codex 专项测试 | 1,700 行 |
| 触及这五个模块的提交数 | 17 个（2026-08-22 → 2026-09-08） |

首个提交还必须改动既有代码才能容纳第二个 runtime：`claude_sdk.py`（+21）、`core/models.py`、`core/ports.py`、`core/manifest.py`、`context/checkpoint.py`、`storage/platform_repositories.py`（+62）、`worker/orchestrator.py`（+30）、`composition.py`、`api/dependencies.py`、`config.py`。后 12 个提交是约三周的补齐与加固（Daytona 隔离、会话恢复、Studio MCP 接线、线程持久化、子线程隔离、长循环、Docker sandbox）。

`context/checkpoint.py:66` 现在显式硬编码两个 runtime 字面量，worker 的 `orchestrator.py:1327,1341,1370` 也是白名单式判断——每加一个 runtime，这类分支都要再多一叉。

### 第二层：能力缺口（第二个 runtime 实际丢掉的东西）

Codex 的经验值：新 runtime 最先静默丢失的是下面这些，而且都成了被接受的缺口：

- **工具级策略门。** Claude 路径用 `sdk_tool_gate.py` 的 `PreToolUse` hook 拦每一次工具调用；Codex 路径只有 `codex_tool_gate.py:28-43`，仅在 Codex 主动上报 `item/commandExecution/requestApproval` 时才评估策略，常规沙箱内工具调用绕过策略引擎。
- **配额与上下文遥测。** Codex 发 `usage.updated`，但仓库里没有任何消费者；Codex 从不发 `runtime.result`，因此不走 run-result 配额结算路径，控制台也拿不到上下文窗口数据。
- **显式 artifact 发布。** `registry_codex_runtime.py` 及同目录 Codex 文件中不存在 `artifact` 字样，Codex agent 只能写 `outputs/**` 靠事后扫描收集。
- **in-process memory / knowledge 工具。** Codex 仅 `remote_memory_mcp.attach(...)`，默认部署下是 no-op；知识问答被 `agui/service.py:270-272` 直接拒绝。
- **manifest `builtin_tools` 形同虚设。** Codex 路径校验了工具目录却不读 `resolved.builtins`。

### 第三层：平台语义被绕开（DeepAgents 特有，Codex 没有）

这是 DeepAgents 与 Codex 的本质差别。Codex app-server 和 Claude Code 是同一类东西——外部 agent 进程，通过协议把事件交给平台，loop 的决策仍在外面。DeepAgents 是**进程内的 agent harness**，它自带 loop 的决策权，因此会和控制平面抢方向盘。

| DeepAgents 自带 | 平台已有 | 冲突性质 |
| --- | --- | --- |
| 虚拟文件系统后端（`ls`/`read_file`/`write_file`/`glob`/`grep`） | 真实沙箱 workspace + `application/workspaces.py:74-232` 的归档/恢复与内容寻址 | 平台需要能 tar、指纹、哈希并发布 workspace；state-backed 虚拟 FS 对 `archive()` 和 `_publish_workspace_outputs()` 不可见 |
| `task` 子 agent 工具 | `SubagentSpec`（`core/manifest.py:121-134`）+ 版本钉死的 `resolve_published_agent_versions` + `subagent_governance.py`（单层、fail-closed、配额） | DeepAgents 的通用 `task` 会同时绕过注册表和治理器 |
| `AGENTS.md` 文件记忆 | `MemoryBankService`（consent / retention / sensitivity）+ `UserMemory` CAS 版本 | 文件式记忆绕过同意与保留策略，且工作区根目录的 `AGENTS.md` 会被归档，静默变成用户可见状态 |
| `interrupt_on` 审批（LangGraph `interrupt()`） | `ApprovalService`（`application/approvals.py:102-403`）+ `RunStatus.WAITING_APPROVAL` + TTL reaper + CAS 决策 + SSE/REST 面 | **最重的一条。** 一个 Run 出现两套暂停/恢复机制；`state_machine.py:27-35` 与 `fencing_token` 模型假定只有 Harness 能翻转 `running ↔ waiting_approval` |
| LangGraph checkpointer（thread_id / checkpoint_ns / state） | `context/checkpoint.py` 的 transcript hash + `SessionContextDigest` 投影 | 两套 checkpoint 键不同源，恢复路径会分叉；平台目前只保证 SDK session 恢复，不承诺任意工具步骤恢复 |
| skills 目录 | `SkillSnapshot` 内容哈希后 base64 钉进 manifest，物化到 `.claude/skills` | 目录约定相同、来源不同，会有优先级歧义 |
| LangSmith 追踪 | OTel + Langfuse 形状属性，`Run.trace_context` 跨进程持久化 | 重复而非冲突，但同一 Run 会产生分叉的 trace 树 |
| `write_todos` 规划 | **无** | 净新增，无冲突 |
| summarization / prompt caching | 只有观测与 rebase 策略（`context/window.py`、`application/sessions.py:184-217`），没有执行侧 | 基本净新增 |

另有两条跨切面约束：`RunStatus` 转移表是封闭的（`core/state_machine.py:6-47`），任何在 `transition()` 之外挂起/重试的外层图都会违反它；以及 Run 的每个对外可见事实都是带单调 `sequence` 唯一约束的持久 `RunEvent`，LangGraph 自己的状态通道会成为同一 Run 的第二份、未对齐的事件日志。

## 好消息：DeepAgents 比 Codex 更容易接的地方

不应只讲成本。DeepAgents 是进程内 Python 库，这在两处反而优于 Codex：

- **工具体可直接复用。** `memory_tools.py`、`artifact_tools.py`、`web_tools.py`、`sandbox_tools.py` 里的 handler 是普通的 `async def (dict) -> dict`，其 `input_schema` 已是 JSON Schema，可直接转成 LangChain `StructuredTool`，不需要像 Codex 那样重写一套 MCP 传输（`registry_codex_runtime.py:69-132` 的 TOML 编码器）。
- **凭据不进子进程。** Codex 必须把 key 以 `HARNESS_CODEX_PROVIDER_API_KEY` 形式交给子进程并靠 TOML 覆盖注入；DeepAgents 在 worker 进程内构造 client，少一层 env 泄漏面。

## 坏消息：远程沙箱

`daytona_transport.py` 从头到尾是 Claude CLI 形状的：`build_remote_claude_command`（`:65-99`）拼的是 `--output-format stream-json`、`--session-mirror`、`--strict-mcp-config` 等 Claude Code 参数，还 import 了 SDK 私有模块 `claude_agent_sdk._internal.session_resume`。DeepAgents 没有可跑的 CLI，远程执行需要全新的 transport。同时 `claude_sdk.py:905-915` 的约束意味着远程模式下 in-process 工具必须改走 HTTP MCP——这条对 DeepAgents 同样成立。

## 已有代价证据：多 runtime 已经产生过一次生产事故

`docs/public-opinion-codex-route-incident-20260910.md:47` 记录了模型路由在 runtime 之间的耦合事故。根因代码在 `registry_codex_runtime.py:199-202`：

```python
# Agent-wide binding is shared with Claude versions, so applying
# it here makes one runtime's route silently break the other
apply_agent_binding=False,
```

一个模型对应两条 route，agent 级绑定被两个 runtime 共享，结果是已发布版本被静默孤儿化。这不是 Codex 的实现失误，而是"同一份 manifest 语义被两个 loop 分别解释"的必然摩擦。每多一个 loop，就多一类这样的耦合面。

## 真正净新增的只有两件事

把上表按"平台已有 vs 净新增"过滤一遍，DeepAgents 对平台的价值收敛为两点：

1. **`write_todos` 任务规划。** 仓库内 grep `write_todos`/`todo` 在 `src/` 下无实现，规划目前只以 `context/service.py:286` 的 `open_tasks` 形式出现在 Digest 里。这是真实缺口。
2. **上下文压缩与 prompt caching。** 平台现在只做观测（`context/window.py:17-19` 的 65/75/85 阈值）与 rebase（会话克隆），不做执行侧压缩。

两项都可以在现有 loop 内落地，并且第二项在 Claude Agent SDK 侧本就有对应能力可用。其余能力引入 DeepAgents 只会削弱平台现有的更强约束（审批的持久化、记忆的同意与保留、子 agent 的版本钉死）。

## 如果决定要做

前置条件必须先定，否则会重演 Codex 的三周补齐：

1. **明确允许丢失的平台语义清单**，并由 `studio/compiler.py` 的能力门（`runtime_*_unsupported`）强制暴露，不能像 Codex 那样先静默丢失再补。
2. **确定 `RunStatus` 与 LangGraph `interrupt()` 的从属关系**：只允许 Harness 驱动状态转移，图的挂起必须映射为 `WAITING_APPROVAL`，禁止双重执行者。
3. **决定 checkpointer 的唯一权威**：要么禁用 LangGraph 持久化、只保留平台 digest，要么明确二者同步方向。
4. **能力契约同步点**（一个 runtime 落地需要同时改四处）：`tests/fixtures/runtime/runtime_capabilities_v0.json`、`studio/catalog.py` 的 `default_capability_catalog()`、`runtime/installed.py:11-14`、以及两个 composition root（`composition.py:1009-1040` 与 `api/dependencies.py:838-868`），再加上 Web 控制台镜像的 `web/harness-console/tests/runtime-capabilities-contract.spec.ts`。
5. **预算**：按 Codex 的实际值准备，即约 2,100 行 runtime + 约 1,700 行测试 + 12 个以上的加固提交，而不是首个提交的规模。

## 建议

短期不做。把 DeepAgents 的两个净新增能力（规划、上下文压缩）移植进现有 loop，风险面仅在 SDK 侧，不需要动 runtime 注册表、能力契约和前端契约。若未来出现明确的、Codex 与 Claude 都覆盖不到的部署需求（例如必须跑在 LangGraph Platform 上），再按上面的前置条件立项，并把它当作与 Codex 同量级的扩容来预算。
