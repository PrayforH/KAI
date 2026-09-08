# AXIS · Agent Operations Platform

AXIS 是面向组织的智能体任务、能力与运行治理平台。它以 Claude Agent SDK 为执行内核，把智能体定义、任务运行、能力装配、协作权限、评测发布和审计治理收束到一条可恢复的执行链路中。

当前发布候选为 **AXIS 0.2.0**。Claude Agent SDK 提供 Agent Loop、工具、Skills、
Sub Agent、MCP、Hooks 与 Session Resume；本项目补齐多租户服务、版本治理、分布式 Run、
可恢复审批、隔离执行、统一 API/Web、上下文工程、评测和可审计发布，不重写 SDK 的执行语义。

| 平面 | 产品能力 |
| --- | --- |
| 定义 | 智能体构建、Draft、能力目录、确定性 Bundle、不可变 Version |
| 质量 | 静态门禁、真实 Preflight、Eval/Quality Gate、发布证据 |
| 运行 | API、Redis Queue、Worker、Claude SDK、Sandbox、MCP、AG-UI |
| 运营 | Deployment Snapshot、test/canary/production 晋级、Trace、回滚、审计 |

## 这套 Harness 的价值

- **让 Agent 代码只关注业务能力。** Manifest 固化 prompt、tools、skills、subagents、权限和预算；平台负责 Session、Run、Workspace、Artifact 与失败恢复。
- **保留 Claude Agent SDK 的能力边界。** 直接使用官方 SDK、SessionStore 和消息类型，不用 LangGraph 重写 Claude Code 的执行语义。
- **new-api 可以作为优先模型网关。** 通过 `ANTHROPIC_BASE_URL` 与 `ANTHROPIC_AUTH_TOKEN` 注入 Anthropic-compatible 网关；能力不满足时只走 Manifest 明确声明的官方回退，绝不静默切换。
- **复杂 Agent 可安全落地。** 工具调用经过 `allow / deny / ask` 策略，审批可暂停、过期、拒绝或恢复；Run 有幂等键、状态机和 fencing token。
- **通用能力可以按需组合。** 默认验证包只依赖模型网关；舆情等领域包再显式组合只读 Tavily 与受限 helper 子 Agent，共享同一套策略、审批和运行界面。
- **从单机验证平滑走向生产。** 核心依赖端口抽象；本地用 Fake Runtime/内存组合，持久化契约已在 PostgreSQL、Redis、MinIO 上验证。
- **前后端协议解耦。** Harness 事件是权威事实，AG-UI 只是无状态投影；assistant-ui 控制台可以替换，而不会影响 Agent 执行层。
- **可观测但不绑定厂商。** 应用只发 OpenTelemetry；生产可由 Collector 输出到 Langfuse，本地默认完全关闭 exporter。

这意味着后续开发一个新 Agent，主要工作变成“写 Manifest + prompt/skills/tools + 受控业务 MCP”，而不是每次重建会话、审批、网关、事件流、存储和 UI。Python SDK MCP 只适用于 Claude CLI 与 Worker 同进程的受信本地模式；生产 Daytona 或 Kubernetes/gVisor 模式应使用带执行身份认证的 HTTP MCP。

## 快速开始

要求 Python 3.12、uv、Node.js 22 和 Docker。macOS 本地 Docker 使用 Colima；向 174 发布的
运行镜像显式构建为 `linux/amd64`。

```bash
uv sync --group dev
uv run harness agent init invoice-reviewer --template analyst --domain accounts-payable
uv run harness agent check agents/invoice-reviewer/agent.yaml --environment production
uv run harness agent pack agents/invoice-reviewer/agent.yaml --output dist/agents
cd web/harness-console && npm ci --registry=https://registry.npmmirror.com && cd ../..
make dev-up
```

平台完整架构、AXIS 构建区、Lead + Sub、多层记忆、安全、评测、发布和分阶段路线见 [docs/agent-production-platform-design.md](docs/agent-production-platform-design.md)，个人 Agent、团队空间 RBAC、共享版本/知识库与任务隔离见 [docs/team-spaces.md](docs/team-spaces.md)，Webhook、A2A 1.0、AG-UI、MCP 职责边界与 Bundle 可逆交付见 [docs/external-agent-exposure.md](docs/external-agent-exposure.md)，可直接交给 Codex/Claude 执行的目标树和循环见 [docs/plans/2026-07-16-agent-production-platform-goals-and-loops.md](docs/plans/2026-07-16-agent-production-platform-goals-and-loops.md)。领域 Agent 的 prompt、Skill、Python Tool、外部 MCP、权限、bundle 发布与评测流程见 [docs/domain-agents.md](docs/domain-agents.md)，单 Agent 上线检查见 [docs/production-agent-runbook.md](docs/production-agent-runbook.md)，平台构建一次、签名、环境晋级和回滚见 [docs/runbooks/release-promotion.md](docs/runbooks/release-promotion.md)。仓库中的 `public-opinion-agent` 是可运行的编排型参考实现。

当前平台版本与发布说明见 [CHANGELOG.md](CHANGELOG.md)。正式 Release 会校验 Python、Web、
Helm 和 Changelog 使用同一 SemVer，并把该版本与 source commit、镜像 digest、SBOM 和 Agent
Bundle hash 一起写入签名 manifest。

生产形态 Docker Compose（API、Worker、Web、PostgreSQL、Redis、MinIO、migration、可选 Langfuse Collector）见 [docs/deployment.md](docs/deployment.md)。构建默认使用清华 PyPI 与 npmmirror，可通过 `.env.docker` 覆盖：

```bash
cp deploy/docker-compose/.env.docker.example deploy/docker-compose/.env.docker
make docker-build
make docker-up
```

使用 cc-switch 当前已应用的 Claude Provider 启动真实模型模式：

```bash
make dev-up-cc-switch
```

该命令只在 API 启动时读取 `~/.claude/settings.json`，不会把网关令牌写入项目。cc-switch 切换 Provider 后需要重启 Harness。

打开：

- Harness API / Swagger: <http://127.0.0.1:8000/docs>
- Harness Console: <http://127.0.0.1:3000>
- MinIO Console: <http://127.0.0.1:9001>

本地栈只有 PostgreSQL、Redis、MinIO；**不包含 Langfuse 或 OTel Collector**。`make dev-up` 会显式设置 `HARNESS_OTEL_ENABLED=false`。

## 验证

```bash
make verify
make agent-pack
make e2e
make web-test
make web-build
```

无模型密钥的 E2E 会验证：Manifest 发布、Session/Run、SSE/AG-UI、工具审批、恢复、Artifact 下载与哈希、终态成功，以及本地 OTel exporter 关闭。

`make verify` 还会对 `agents/*/agent.yaml` 执行生产门禁；`make agent-pack` 为所有通过门禁的 Agent 生成确定性 ZIP。生产发布 API 只接受 `/v1/agents/bundles`，不会读取客户端提交的服务器本地路径。

本地 `make verify` 只从 `deploy/docker-compose/.env.docker` 读取 PostgreSQL、Redis 和 MinIO 的测试连接参数，幂等创建独立的 `harness_test` 数据库，并默认关闭测试遥测；不会把模型、MCP 或 Langfuse 凭据导入测试进程。CI 显式提供的 `HARNESS_TEST_*` 变量始终优先。

Web 首页直接使用 assistant-ui 的 Thread、Composer、Attachment 与 Markdown primitives，并通过官方 AG-UI runtime adapter 连接 Harness。主对话以一行可展开的执行条呈现工作摘要、工具和子 Agent，JSON/代码/Diff 使用结构化卡片；“运行详情”提供 Harness 事件脊柱与模型、Provider、时长、轮次、成本、停止原因。`make dev-up` 使用 Fake Runtime；`make dev-up-cc-switch` 使用当前 cc-switch Provider。两种模式都会幂等发布 `helper-agent@1.0.0` 与正式验证包 `echo-agent@0.4.1`，不会再把 deterministic test fixture 发布给网页。以下审批与产物标记仅用于 Fake Runtime 验收：

```text
[approval] [artifact] 验证完整流程
```

审批卡片选择“批准并继续”后，同一 Run 自动恢复并显示 `result.txt` 下载卡片。页面刷新会复用本地 thread ID 且不会重复创建 Run；Phase 1 尚未把耐久事件接成 assistant-ui 的历史消息 adapter，因此刷新后不会自动恢复聊天正文。原始 AG-UI 活动、消息和状态可在“运行详情”中核对。

点击“＋ 文件”上传本地文件后，Next.js 同源 BFF 会创建 InputArtifact；消息发送时只携带服务端 ID。Worker 校验归属后将文件以只读方式挂载到本次 Run 的 `inputs/`，Claude Agent SDK 可使用 `Read` 读取。浏览器路径、内联字节和伪造 URL 不会被信任为工作区输入。

真实模式可用下列问题验证工作区写入与低打扰权限判定：

```text
在当前工作区创建 outputs/hello.md，写入一段中文说明，然后读取文件确认内容。
```

`echo-agent@0.4.1` 显式声明 `Read/Glob/Grep/Write/Edit/Bash/Task`，只配置模型网关即可运行；需要联网检索的领域包（例如 `public-opinion-agent`）再显式声明 `mcp: tavily-readonly`。`Write/Edit` 仅能操作本次 Run 的 workspace，常规 `Bash` 在本地 workspace 与隔离容器内默认自动放行；工作区不可逆删除、越界路径、未知工具与显式策略拒绝仍会被阻断，真正需要用户判断的敏感边界才进入人工审批。Anthropic 官方受支持 Claude 型号还会在 Harness 决策后启用 Claude Code `auto` 权限分类器。Manifest 始终是工具能力上限，Sandbox 策略不能给 Agent 注入未声明工具。

输入 `[slow] 验证停止` 并在消息开始后点击停止按钮，可以验证浏览器流中止、同源 AG-UI BFF 取消映射及 Harness Run 最终进入 `cancelled` 的完整链路。

new-api 实际连通性是显式的可选 smoke：

```bash
NEW_API_BASE_URL=https://gateway.example/api \
NEW_API_KEY=... \
NEW_API_MODEL=claude-sonnet-4-6 \
uv run python scripts/smoke_new_api.py
```

未提供三个变量时脚本安全跳过。详细说明见 [docs/local-development.md](docs/local-development.md)。

## 当前边界

当前仓库已经具备持久化生产组合根、API 服务 Bearer、带 visibility lease/心跳/崩溃回收的 Redis Run 队列，以及 Docker Compose 和 Kubernetes/gVisor per-run Pod 两条部署基线。gVisor Pod 使用只读根文件系统、临时写层、无 Token ServiceAccount、默认拒绝网络和 allowlisted egress proxy；TTL Reaper 负责回收 Worker 崩溃遗留实例。Helm 资产与集群 opt-in E2E 见 `deploy/helm/agent-harness` 和 `tests/integration/sandbox/test_kubernetes_gvisor_live.py`。平台仍需继续补齐租户配额/计费和长期事件订阅。主 Agent 已能解析 builtin、受信本地 Python SDK MCP 和服务端注册的外部 HTTP MCP，并通过 `PreToolUse` 在真实 SDK 执行前完成基于可信 Sandbox 隔离级别、Run 上下文信任等级的策略与审批；外部网页工具成功返回后会把上下文单调标记为 `untrusted`，阻止提示注入内容进入长期记忆。API/Worker 间的审批决策已经通过耐久 Repository 传播。远程 Sandbox 会拒绝无法跨进程传递的 `python_entry`，而不是静默丢失工具。

## License

[Apache-2.0](LICENSE)
