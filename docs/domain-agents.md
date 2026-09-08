# 快速构建生产领域 Agent

可以使用 Claude Agent SDK 配合系统提示词、Skills 和 Tools 构建业务 Agent，但这四项只解决“Agent 会做什么”。Harness 继续统一解决“如何安全、稳定、可验证地运行”：版本、模型网关、权限、审批、Sandbox、Session/Run、文件、产物、事件、评测和 Langfuse/OTel。

领域代码不应 fork Harness。一个业务 Agent 是一个可版本化目录，平台升级和业务迭代可以独立进行。

## 1. 选择模板并生成目录

```bash
# 只读分析、检索、归纳
uv run harness agent init invoice-reviewer \
  --template analyst \
  --domain accounts-payable

# 会修改文件或调用写操作
uv run harness agent init campaign-operator \
  --template operator \
  --domain marketing-operations

# 会把任务委派给子 Agent
uv run harness agent init public-opinion-orchestrator \
  --template orchestrator \
  --domain public-opinion
```

生成结构：

```text
agents/invoice-reviewer/
├── agent.yaml                  # 版本化运行契约
├── prompts/system.md           # 角色、工作流、安全边界、输出契约
├── skills/
│   └── invoice-reviewer-core/
│       └── SKILL.md            # 业务 SOP，可继续带 references/scripts/assets
├── tools/README.md             # 领域工具接入约定
├── evals/suite.yaml            # happy/ambiguous/safety 回归集
└── README.md
```

生成物是生产形状，不是已经完成的业务实现。必须把模板中的目标、证据、工具、测试和输出字段改成真实领域语义。

## 2. 把职责写进正确的层

### 系统提示词：稳定的行为契约

生产门禁要求以下五段：

- `Mission`：负责什么业务结果，以及明确不负责什么。
- `Operating workflow`：正常路径、缺少输入、失败和停止条件。
- `Evidence and tool use`：事实来源、引用规则、如何验证工具结果。
- `Safety boundaries`：敏感数据、越权、审批、外部内容和 prompt injection。
- `Output contract`：用户最终可消费的字段、格式和质量条件。

不要把大量易变知识塞进系统提示词，也不要要求模型暴露隐藏思维过程。提示词负责原则和协议；业务 SOP 放 Skill；确定性规则放代码和 Policy。

### Skills：可复用的领域 SOP

每个 Skill 是带 YAML frontmatter 的目录：

```markdown
---
name: invoice-review
description: Review invoice evidence and identify approval exceptions.
---

# Invoice review

1. 校验发票、订单和收货记录的业务标识。
2. 分别列出已验证事实、推断和缺失证据。
3. 命中例外规则时请求审批，不得自行放行。
```

发布时 Harness 会递归快照 Skill 的 `SKILL.md`、references、scripts 和 assets，计算内容哈希。运行时从不可变快照写入 `.claude/skills/<name>`；后续修改源目录不会改变已经发布的 Agent 版本。

Skill 必须小而聚焦。跨多个 Agent 复用的审核规则或报告规范可以独立成 Skill；凭据、环境地址和租户数据不能放入 Skill。

新建草稿默认不安装 Skill。平台提供经过审核的 Skill 包目录，但目录项不是运行时引用：
Builder 将选中的包按明确 revision 复制成草稿自己的 `DraftSkill`，记录来源、许可证和
源内容哈希，并把包携带的回归场景合并进草稿评测集。发布时这些内容继续随 Agent
版本递归快照和冻结；平台包后续升级不会改变已经导入或发布的版本。导入后修改内容会
保留来源记录并标记为已修改，不再显示为平台当前原始快照。

当前内置目录只包含无需外网、凭据、脚本和额外依赖的流程型 Skill：证据化报告、
多源研究归纳、交付结果核验、文档与表格制作流程，以及 Skill 创建与质检。文档和
表格 Skill 只规定制作与验收流程；确定性生成、转换、文件处理和外部系统访问仍应
实现为工具或 MCP，而不是伪装成 Skill。

其中 Skill 创建与质检基于 Apache-2.0 授权的 OpenAI `skill-creator` 固定 commit
制作离线适配版，并在包内保留上游地址、commit 和修改声明。第三方 Skill 不能只凭
热度进入目录；必须逐包确认许可证、固定源 revision、扫描脚本与依赖，并通过平台评测。

### Tools：确定性能力和副作用边界

SDK builtin 适合工作区和委派：

```yaml
tools:
  - builtin: Read
  - builtin: Glob
  - builtin: Grep
  - builtin: Write
  - builtin: Edit
  - builtin: Bash
  - builtin: Task
```

Python 领域工具适合低延迟的内部查询和计算：

```yaml
tools:
  - python: billing_agent.tools:lookup_invoice
```

外部 MCP 适合独立部署、独立凭据或跨语言系统：

```yaml
tools:
  - mcp: crm-readonly
```

Manifest 中的 MCP 值是服务端注册的逻辑 ID，不是 URL。真实命令、Endpoint、Header 和 Token 由服务器注入。未注册的 MCP 会 fail closed。

执行位置必须在设计工具时明确：

| 工具类型 | 实际执行位置 | 安全责任 |
|---|---|---|
| Claude builtin（Read/Write/Edit/Bash/Glob/Grep） | Daytona sandbox 内的 Claude CLI | Sandbox 文件/进程/网络隔离 + Policy/审批 |
| Python SDK MCP（含 memory、artifact、`python_entry`） | 仅限 Claude CLI 也在 Harness worker 的本地执行模式 | 仅允许受信代码；Daytona 远端执行时必须改成认证 HTTP MCP，禁止伪序列化 Python `instance` |
| 外部 MCP | MCP 服务所在环境 | MCP 服务自身认证、授权、审计、限流和隔离 |

因此 Daytona 是模型与内建文件/命令工具的安全执行后端，不会自动包住 worker 内的 Python 扩展。远端 Daytona 也无法调用只存在于 worker 内存里的 SDK MCP `instance`；Harness 会对此 fail-fast，而不是静默丢失工具。需要运行不受信业务代码时，应把它做成 sandbox 内进程；需要访问平台/业务能力时，应做成 sandbox 可达、带执行身份认证的 HTTP MCP 服务，不能作为 `python_entry` 直接加载到 worker。

Daytona Agent 需要向用户交付文件时，直接在沙箱 workspace 的 `outputs/` 下写普通文件。Harness 在 Run 结束后同步该目录，重新检查路径、symlink、文件数量和累计字节，再发布为 Artifact；不要让模型通过控制面本地路径发布文件。

每个写工具都应满足：参数 schema 严格、租户归属校验、幂等键、结构化结果、超时、重试边界，以及 allow/deny/ask 测试。不要用 prompt 替代这些确定性约束。

服务端 MCP Registration 还必须声明工具结果是否可信。网页搜索和抓取结果标记为
`untrusted`；成功调用后，Run 的上下文信任等级只升不降，后续工具策略会自动收紧。
失败调用不改变信任等级。该状态跨 Lead/Sub 委派传播，且审计事件不保存原始网页
正文。外部 MCP 不得依赖 Prompt 自报“这是安全内容”来改变结果信任等级。

### Subagents：只在职责边界清楚时使用

```yaml
tools:
  - builtin: Task
subagents:
  - ref: helper-agent@1.0.0
    alias: evidence-researcher
    description: 收集证据并返回带来源的事实，不负责最终结论。
    background: true
  - ref: helper-agent@1.0.0
    alias: risk-reviewer
    description: 独立挑战关键判断并标记反例与不确定性。
    background: true
```

子 Agent 必须固定版本；生产禁止 `latest`。一个通用版本可以通过不同 `alias`
绑定为多个职责，`description` 用于让 Lead 选择正确角色，`background` 允许独立任务
并行运行。Lead 必须负责拆解、验收和最终汇总。适合委派可独立验收的检索、归纳
或专业审查，不适合为了“看起来像多智能体”拆分简单流程。当前仅支持一层委派，
Sub Agent 只使用自己的 builtin tools、Prompt、Skills、Policy 和轮次上限。

仓库中的 `public-opinion-agent@0.1.2` 展示了 `mcp: tavily-readonly`、`helper-agent@1.0.0`、证据引用、风险分级和中文报告契约。外部搜索由 Lead 直接执行，子 Agent 只归纳工作区证据。它复用共享审批与运行界面，没有另建事件协议或 Web UI。

## 3. 选择最小权限 Profile

Manifest 只能选择服务端注册的 Profile，不能自己定义放行规则：

| Profile | 使用场景 | 默认边界 |
|---|---|---|
| `production-read-only` | 分析、检索、报告 | Read/Glob/Grep 和审核过的只读 MCP |
| `production-standard` | 文件或业务写操作 | 写工具按 Sandbox 事实与规则 allow/ask/deny |
| `production-orchestrator` | 带 Task 的编排 Agent | 主 Agent 和子 Agent 都受各自 Manifest 上限约束 |

策略在两条路径都执行：真实 Claude SDK 的 catch-all `PreToolUse` Hook 会在工具执行前拦截；Worker 事件门也约束 Fake/替代 Runtime。这样测试路径和生产路径不会出现权限漂移。

Daytona 等容器隔离保护宿主机，但不自动等于允许所有 Bash。模型网关凭据、网络出口和外部副作用仍需要单独控制。

## 4. 校验和评测

开发期结构校验：

```bash
uv run harness agent validate agents/invoice-reviewer/agent.yaml
```

生产门禁：

```bash
uv run harness agent check \
  agents/invoice-reviewer/agent.yaml \
  --environment production
```

门禁检查版本、domain、模型能力、预算、Workspace 归档、已注册 Policy、固定子 Agent、系统提示词五段、Skill 快照、敏感文件、文件大小和评测覆盖。

每个 Agent 至少维护：

- `happy`：完整输入下获得正确业务结果。
- `ambiguous`：缺少标识或证据时澄清，而不是猜测。
- `safety`：越权或不可逆动作被拒绝或进入审批。

需要文件证据的 case 把夹具放在 Agent 包中，并声明相对路径；Runner 会通过 InputArtifact API 上传，不能在 prompt 中假装文件已经存在：

```yaml
inputFiles:
  - path: evals/fixtures/invoice.txt
    mediaType: text/plain
```

连接正在运行的 Harness 做真实回归：

```bash
uv run harness agent eval agents/invoice-reviewer/agent.yaml \
  --base-url http://127.0.0.1:8000/v1 \
  --tenant eval \
  --user ci-evaluator \
  --publish \
  --json \
  --junit work/evals/invoice-reviewer.xml
```

每个 case 使用独立 Session 和稳定幂等键。Runner 根据耐久 Run/Event 判断终态、工具、审批、输出文本和耗时；单个网络或控制面错误会记录为失败并继续整套评测。

## 5. 打包和发布

```bash
uv run harness agent pack \
  agents/invoice-reviewer/agent.yaml \
  --output dist/agents
```

ZIP 文件名包含版本与完整 package hash，同一内容可重复构建出完全相同的字节。`bundle.json` 同时保存运行时 Manifest hash、完整 package hash 和每个文件的摘要；只改评测集也会产生不同的 package hash，不能覆盖原发布物。

本地开发 API 可以使用服务器可见路径。生产 API 禁止该方式，只接受 bundle：

```bash
curl --fail-with-body \
  -X POST http://127.0.0.1:8000/v1/agents/bundles \
  -H 'Content-Type: application/zip' \
  -H 'X-Tenant-ID: acme' \
  -H 'X-User-ID: release-bot' \
  --data-binary @dist/agents/invoice-reviewer-0.1.0-<hash>.zip
```

服务端以流式方式读取上传，在 25 MiB 压缩体积上限处立即停止，并重新执行生产门禁；同时校验媒体类型、路径穿越、符号链接、重复路径、文件数量、解压后大小和 provenance 哈希。已发布的 `name@version` 不可覆盖；任何修改都必须提升版本。

仓库级检查：

```bash
make agent-check       # 检查 agents/*/agent.yaml
make agent-pack        # 全部输出到 dist/agents
make verify            # Ruff + Pyright + Agent 门禁 + Pytest
```

## 6. 推荐迭代顺序

1. 用 10–30 个真实历史任务定义结果和失败样例。
2. 先做单 Agent + 最小只读工具，稳定输出契约。
3. 把高频 SOP 提取为 Skills，把确定性校验写成工具或 Policy。
4. 只有出现清晰专业边界时才加入 Subagent。
5. 用 live eval 比较新旧版本；同时观察成功率、审批命中、耗时、成本和人工修订量。
6. 先在新 Session/小流量发布；异常时停止创建新 Session 并回到上一固定版本。

上线和回滚检查表见 [production-agent-runbook.md](production-agent-runbook.md)。

## 当前边界

- 主 Agent 支持 builtin、Python 和已注册 MCP；subagent 当前只支持 builtin。
- 公网认证、TLS、配额和可信身份头应由 API Gateway/部署层提供。
- API 与 Worker 分进程时，审批决策通过耐久 Repository 传播；敏感工具参数只保留脱敏后的审计副本。Redis Run task 使用 visibility lease、心跳和崩溃回收；等待审批后的自动 continuation 与 Daytona 网络出口策略仍需继续强化。
- `timeoutSeconds` 是完整 SDK 执行的墙钟上限，包含工具与审批等待；命中后 Run 以 `runtime_timeout` 进入 `timed_out`。
- Eval Runner 已提供确定性协议门禁，但领域正确率仍需要业务数据集和人工标注，不能仅靠三个模板 case。
