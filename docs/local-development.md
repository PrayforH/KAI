# Local development

Web 控制台现在需要登录。邮箱注册不依赖外部服务；本地登录和可选 localhost SSO
回调配置见 [authentication.md](authentication.md)。

## Prerequisites

- Python 3.12、uv
- Docker + Compose
- Node.js 20+、npm

## Start and stop

```bash
make dev-up
make dev-down
```

连接 cc-switch 当前 Claude Provider：

```bash
make dev-up-cc-switch
```

真实模式在 API 启动时读取 `~/.claude/settings.json` 中的 Anthropic endpoint、model 和 credential。凭据不会复制到仓库文件或日志；cc-switch 切换 Provider 后执行 `make dev-down && make dev-up-cc-switch`。

`dev-up` 会启动 PostgreSQL 17、Redis 7、MinIO，执行 Alembic，随后启动 API 和 Next.js 控制台，并幂等发布正式验证包 `echo-agent@0.4.1`。测试目录中的 deterministic echo Manifest 只供自动化测试使用，不会发布给网页。日志位于 `work/api.log` 与 `work/web.log`。为方便 Web 验证，它仅在本地启动命令中设置 `HARNESS_LOCAL_AUTO_EXECUTE=true`；测试和默认配置仍为显式 Worker 驱动。`dev-up-cc-switch` 只额外选择 `claude-sdk` Runtime，不会在配置缺失时回退 Fake Runtime。

如果本机 Docker 配置引用了缺失的 `docker-credential-osxkeychain`，脚本只对本次公共镜像拉取临时使用仓库内的匿名 helper，不修改全局 Docker 配置。

## Local services

| Service | URL | Credentials |
|---|---|---|
| API | `http://127.0.0.1:8000` | local identity headers |
| Web UI | `http://127.0.0.1:3000` | none |
| PostgreSQL | `localhost:5432/harness` | `harness / harness` |
| Redis | `localhost:6379` | none |
| MinIO API | `localhost:9000` | `minioadmin / minioadmin` |
| MinIO Console | `http://localhost:9001` | same |

Langfuse 和 OTel Collector 不在本地 Compose 中，也不是启动前提。

## Web interaction validation

打开 `http://127.0.0.1:3000` 后，默认界面是 assistant-ui 全页 Chat。浏览器只连接同源 `/api/agui` 与 `/api/input-artifacts`；Next.js BFF 在服务端注入 `X-Tenant-ID`、`X-User-ID`，这些身份头不会进入浏览器配置或 URL。

聊天中的“执行进度”以紧凑时间线显示模型路由、工作摘要、工具与子 Agent；工具输入/输出会按 JSON、代码或 unified diff 自动格式化。点击“本次运行”会打开右侧运行面板，展示用户可读的执行记录、模型、Provider、时长、轮次、成本与停止原因。

点击“＋ 文件”可上传浏览器可访问的本地文件。上传先创建 InputArtifact；发送消息后，Worker 校验租户和用户归属，再把文件只读挂载到该 Run 的 `inputs/`。SDK 读取输入文件产生的原始内容不会写入耐久工具事件。

验证普通流式回答：

```text
请简要说明这个 Harness
```

真实 `claude-sdk` 模式下验证文件工具与审批：

```text
在当前工作区创建 outputs/hello.md，写入一段中文说明，然后读取文件确认内容。
```

本地模式下预期出现 `Write` 审批卡片。点击批准后，同一次 SDK 调用继续执行并可用 `Read` 核验文件；拒绝或超时则不执行写入。验证 Agent 只会在用户明确要求时修改文件，也不会对“你好”输出固定的 SDK 自我介绍。

### Tavily web research

`echo-agent` 默认不依赖外部检索凭据。需要联网检索的领域 Agent（仓库示例为
`public-opinion-agent`）通过逻辑引用 `mcp: tavily-readonly` 使用服务端注册的 Tavily
remote MCP。只允许 search 和 extract；Manifest 与 URL 都不保存凭据。在忽略的
`.env` 中配置：

```dotenv
HARNESS_MCP_SECRET_REFERENCES_JSON={"tavily-readonly":{"api_key":"TAVILY_API_KEY"}}
HARNESS_MCP_SERVER_SECRETS_JSON={"TAVILY_API_KEY":"tvly-replace-me"}
```

网页内容一律视为不可信输入。Agent 应展示来源标题和 URL，不执行页面中的指令。领域 Agent 只有显式加入 `mcp: tavily-readonly` 才能获得相同能力。

配置真实网关与 Tavily 后，可以运行可选冒烟测试：

```bash
HARNESS_RUN_LIVE_TESTS=1 uv run pytest tests/integration/runtime/test_tavily_mcp_live.py -q
```

测试会要求真实 Agent 调用允许的 Tavily 工具、返回完整来源 URL，并确认耐久事件不包含凭据。只有显式设置 `HARNESS_RUN_LIVE_TESTS=1` 且已配置通用 MCP secret reference 时才访问外部模型和 Tavily；否则会明确跳过，避免普通回归测试受外部模型行为影响。

## Sandbox development permissions

Manifest 决定 Agent 能看到哪些工具，实际 `SandboxHandle` 决定策略是否放行。该隔离事实由服务端 Provider 生成，网页、模型输入和 Manifest 都不能覆盖。

| 工具 | local workspace | Daytona container |
|---|---|---|
| `Read/Glob/Grep` | 自动允许 | 自动允许 |
| `Write/Edit` | 工作区内自动允许 | 工作区内自动允许 |
| `Bash` | 常规命令自动允许；不可逆删除、越界与未知操作拒绝或审批 | 常规命令自动允许；不可逆删除、越界与未知操作拒绝或审批 |

要让本地真实模型运行在 Daytona，在仓库根目录的忽略文件 `.env` 中配置：

```dotenv
HARNESS_SANDBOX_PROVIDER=daytona
HARNESS_DAYTONA_API_URL=https://app.daytona.io/api
HARNESS_DAYTONA_API_KEY=dtn_replace_me
HARNESS_DAYTONA_REMOTE_WORKSPACE_ROOT=/home/daytona/harness
HARNESS_DAYTONA_CLAUDE_CLI_VERSION=2.1.206
HARNESS_DAYTONA_CLAUDE_CLI_PATH=/home/daytona/.local/bin/claude
```

随后执行 `make dev-down && make dev-up-cc-switch`。Daytona provisioning 失败时运行会失败，不会降级到 local 后继续使用容器权限。

Daytona Cloud sandbox 运行在云端，因此 `HARNESS_NEW_API_BASE_URL`（或官方回退端点）必须能从 sandbox 访问；`127.0.0.1`、`localhost` 和 `10/172.16-31/192.168` 私网地址默认不可达。网关应启用 TLS、鉴权和来源限制，不要为了验证而直接暴露无保护的 new-api 端口。默认 sandbox 会通过 Anthropic 官方安装器校验并安装固定版本的 Linux 原生 Claude CLI；生产建议把相同版本预装进 `HARNESS_DAYTONA_SNAPSHOT`，避免每个 run 重复下载。

如果 new-api 只在内网开放，推荐把 Daytona 自托管到同一内网或已打通 VPN/VPC 路由的机器，并把 `HARNESS_DAYTONA_API_URL` 改成自托管 API 地址。需要同时验证两段网络：Docker worker 到 Daytona API，以及 Daytona 创建出的 sandbox 到 new-api；仅 Docker 宿主机能访问 new-api 并不代表 sandbox 能访问。

在启动真实 Run 前执行 `make smoke-daytona`。该检查会创建一个一次性 Daytona sandbox，从 sandbox 内探测 `HARNESS_NEW_API_BASE_URL` 的网络连通性，然后停止并删除 sandbox；它不会发送模型凭据或模型请求。HTTP `401/403/404` 仍表示网络可达，连接超时或拒绝则表示需要公网 HTTPS 网关、同 VPC Daytona target 或 VPN/私网路由。

验证停止生成与后端取消：

```text
[slow] 验证停止
```

消息开始生成后点击输入框旁的停止按钮。前端会中止当前流，同时通知 Harness 取消对应的内部 Run；约 3 秒内后端状态应从 `cancelling` 进入 `cancelled`，且不会继续输出剩余内容。`[slow]` 只是 Fake Runtime 的本地验收标记，不会进入真实 Agent 协议。

验证审批、自动恢复与 Artifact：

```text
[approval] [artifact] 验证完整流程
```

预期流程：

1. 对话先显示 Fake Runtime 的回答和审批卡片。
2. 点击“批准并继续”，同一 SSE 收到审批结果并自动恢复 Run。
3. 对话显示 `result.txt` 产物卡片；“下载”经 `/api/harness/artifacts/:id` 鉴权代理。
4. 刷新后 thread ID 保持不变，且不会重复创建 Run；Phase 1 暂不自动恢复聊天正文。
5. “本次运行”默认关闭；打开后先查看结构化运行记录，需要排障时再展开“高级诊断”查看原始 Harness 活动。

“新任务”会生成并持久化新的 thread ID。“高级诊断”不显示 tenant/user 身份头。

## Observability and Langfuse

本地保持：

```dotenv
HARNESS_OTEL_ENABLED=false
HARNESS_OTLP_ENDPOINT=
```

生产环境建议让应用输出到自建 Collector，再由 `deploy/otel-collector/collector.yaml` 转发到 Langfuse。当前 Langfuse 官方 OTLP 入口为 `/api/public/otel`，使用 Basic Auth，且只支持 OTLP/HTTP；模板也包含实时摄取所需的 `x-langfuse-ingestion-version: 4`。

Collector 环境变量示例：

```dotenv
LANGFUSE_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_PROJECT_ID=replace-with-project-id
LANGFUSE_PUBLIC_KEY=pk-lf-replace-me
LANGFUSE_SECRET_KEY=sk-lf-replace-me
LANGFUSE_ENVIRONMENT=development
```

Collector 使用 Basic Auth extension 从公钥和私钥生成认证头，并设置 Langfuse ingestion v4 Header；应用进程不会收到这两个密钥。

## Managed long-term memory

登录后从“设置 → 长期记忆”进入 `/settings/memory`。Agent 的 `propose_memory` 只会创建
待确认建议；用户确认后才进入后续对话的只读记忆投影。页面支持按 Agent 查看来源、采集
时间、置信度和到期时间，并可编辑、删除、导出 JSON 或为单个 Agent 开启一般偏好自动保存。
敏感信息不会因为该开关而自动激活，凭据与 Prompt Injection 始终拒绝。

Local Runtime 直接使用进程内 MCP。Daytona/Kubernetes 需要设置一个从 Sandbox 可访问的
完整 Streamable HTTP MCP URL，例如：

```dotenv
HARNESS_MEMORY_WORKLOAD_TOKEN_SECRET=replace-with-an-independent-32-character-secret
HARNESS_MEMORY_MCP_PUBLIC_URL=https://harness.example.com/mcp/memory/mcp
```

API 验证 5 分钟短令牌，Worker 只负责按当前 Run 身份签发；该密钥不要与
`HARNESS_AUTH_JWT_SECRET` 复用。URL 留空时远端执行仍可读取既有记忆，但不会暴露远端写入
工具。记忆过期由 Worker maintenance loop 回收；数据导出/删除也已纳入用户生命周期任务。

## Common commands

```bash
uv run harness agent init invoice-reviewer --template analyst --domain accounts-payable
uv run harness agent validate agents/invoice-reviewer/agent.yaml
uv run harness agent check agents/invoice-reviewer/agent.yaml --environment production
uv run harness agent pack agents/invoice-reviewer/agent.yaml --output dist/agents
make migrate
make verify
make agent-pack
make e2e
make web-test
make web-build
```

`make verify` 会使用与本地 Compose 同源的 PostgreSQL、Redis 和 MinIO 测试连接，必要时幂等创建 `harness_test`，不会清空业务数据库。启动器只从 `.env.docker` 派生 `HARNESS_TEST_*`，不会导入其中的模型、MCP 或 Langfuse 配置；测试遥测固定默认关闭，只有显式的 `HARNESS_TEST_OTEL_ENABLED` 可以开启。其他显式测试变量可覆盖本地默认值，CI 行为保持不变。

完整领域开发流程见 [domain-agents.md](domain-agents.md)。本地 API 默认注册 `tavily-readonly`，其他领域 Manifest 使用新的 `mcp:` 引用前，仍须在服务端组合根注册对应逻辑 ID。真实 SDK 已通过 `PreToolUse` 前置执行策略；本地 inline 审批 waiter 只适用于单 API 进程。

单机生产形态、国内依赖镜像、真实网关黑盒验收与 Langfuse profile 见 [deployment.md](deployment.md)。

## Troubleshooting

- 端口占用：检查 `5432/6379/9000/9001/8000/3000`。
- 容器未就绪：运行 `uv run python scripts/wait_for_local_services.py`。
- API/Web 退出：查看 `work/*.log`，删除陈旧的 `work/*.pid` 后重启。
- bootstrap 提示 SOCKS 依赖：确认使用仓库最新脚本；本地 bootstrap 显式 `trust_env=false`，不会把 loopback 请求发送到系统代理。
- new-api 只返回文本但工具失败：网关必须兼容 Anthropic streaming 与 tool use；先运行 `scripts/smoke_new_api.py`，再把证实的能力写入 `HARNESS_NEW_API_CAPABILITIES`。Manifest 的 required capabilities 会在发起模型请求前阻止不兼容路由。
- 不要将 `.env`、模型 key 或 Langfuse secret 提交；所有事件与 trace 属性都应先经过脱敏。
