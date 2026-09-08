# WebUI 内置联网与 79 验证环境

已部署：http://172.20.105.79:3501 。后续已按用户要求迁移 174 数据，并同步升级 174：http://172.20.109.174:3501 。可使用原账号密码；当前部署、数据隔离与回退说明见 [迁移及双环境升级记录](migration-174-to-79-and-dual-upgrade-20260908.md)。以下保留初次独立部署的实现与验证记录。

## 联网行为

- 默认 `lead-agent:1.0.1` 配置 WebSearch、WebFetch。用户直接提出检索请求即可，无需创建 MCP 连接；其他 Claude SDK 智能体可在工具配置中选择这两项。
- WebSearch 由平台直接调用搜索 HTTP API，79 使用 MiniMax 官方搜索接口和已有平台模型服务凭据。也支持 Tavily；已有 Tavily MCP 用户凭据可在选用 Tavily 且未配置平台搜索密钥时复用。已有其他 MCP 连接仍可使用。
- 搜索使用公开关键词，可多轮搜索和阅读来源；WebFetch 直接读取公开 HTTP(S) 文本网页，不携带用户登录态、Cookie 或环境代理。返回来源、时间、正文和截断标记。
- 用户侧没有新增联网总开关。平台开关由 `HARNESS_WEB_TOOLS_ENABLED` 控制；关闭后声明内置联网工具的运行会明确报错，管理员需移除其工具声明。每个智能体仍以工具声明决定是否有内置联网能力。
- SDK 内部使用进程内工具桥接注册搜索和读取函数，这是运行时实现细节；部署和用户均不需要额外 MCP 搜索服务器。

## 边界

普通公网检索自动允许；敏感参数规则拦截常见密钥、令牌、邮箱、手机号和过长载荷。网页仅允许 HTTP(S)、80/443，不接受 URL 凭据，限制 2 MB 原始响应、24,000 正文字符、25 秒总时间及 5 次跳转。每次连接重新检查全部 DNS 结果并连接到已验证的 IP，TLS 保留原始域名；内网、回环、元数据、CGNAT、IPv6 过渡地址和 HTTPS 降级跳转被拒绝。

搜索和网页内容始终标为 `untrusted`，明确区分外部资料与用户指令，并进入现有上下文信任审计、工具权限和记忆写入策略。以上规则降低注入及外传风险，但敏感信息检测是启发式规则，不能保证识别所有私有业务文字。此次没有重写 Bash 的网络权限；79 延续工作区隔离的 local 执行模式，不能据此宣称已实现全工具网络出口隔离。内置工具暂不支持远程 CLI 和 Codex 运行时，这些场景保留已有 MCP 路径。

## 验收证据

- 前端：80 个文件、522 项测试通过；79 Linux 生产构建通过。
- 后端：联网、SDK 工具接入、策略、编译、目录、生产组合与 AG-UI 共 171 项通过；后续增加 3 项安全测试，联网专项共 29 项通过。
- Ruff、涉及模块 Pyright 与 `git diff --check` 通过。
- 79 容器内：搜索返回 5 条来源；DeepSeek 官网读取成功；`169.254.169.254` 请求在连接前被拦截。
- 真实默认模型任务 `run_54207b4c37f84bdebc243a8af28e0d78` 状态 `succeeded`：DeepSeek 模型先调用 WebSearch，再调用 WebFetch，两个工具结果均成功，最终回答包含官网来源；审计记录显示上下文由 `safe` 升至 `untrusted` 后仍允许公开网页读取。
- 79 生产前端 Playwright：5 条折叠与展开、长标题实际滚动、减弱动效、两浏览器上下文读取同步、新结果未读、默认资产、手动切换、移动端与页面错误检查通过。该浏览器回归使用 API fixtures；真实持久化与用户隔离由后端及 PostgreSQL 集成测试验证。
- 全新 PostgreSQL 18 数据库完整迁移至 0030。修复历史 0001 使用当前 metadata 建表后，0028 重复加列、0030 重复建表的问题；既有库仍执行缺失列创建和数据回填。

## 配置与运维

不含密钥的部署清单及增量镜像配方位于 `deploy/web-79/`。API 镜像 `kai/axis-api:20260908-web79`，Web 镜像 `kai/axis-web:20260908-web79`；基础镜像为已运行验证的 `20260908-101112`，Web 构建依赖基础为 `20260906-004236`。

服务器 `.env` 保存本环境新生成的数据库、对象存储和平台认证密钥；`model.env` 保存复用的平台模型路由；`network.env` 保存 `HARNESS_WEB_TOOLS_ENABLED=true`、`HARNESS_WEB_SEARCH_PROVIDER=minimax` 和搜索凭据，均为 0600 文件，未写入仓库。更换服务时可将 provider 改为 `tavily` 并提供对应凭据，重建 API/worker 容器生效。

构建时将当前 `src/harness`、`agents/lead-agent`、`migrations/versions`、`web/harness-console` 放入发布目录，排除 node_modules、.next 和缓存。基础镜像需预先导入。先构建 API 与 Web，再 `docker compose up -d`。新环境先创建私有 MinIO 桶 `harness-artifacts`；运行中环境不要重建或清空数据卷。

检查：在服务器部署目录执行 `docker compose ps -a`、`docker compose logs --tail=100 worker`。关闭此验证环境可执行 `docker compose stop`，保留数据卷；不要使用删除数据卷选项。后续增量发布应使用新的镜像标签，记录旧标签后再切换，避免覆盖当前版本。

参考：[MiniMax 官方搜索实现](https://github.com/MiniMax-AI/MiniMax-Coding-Plan-MCP/blob/main/minimax_mcp/server.py)、[Tavily 搜索 API](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[HTTPcore 网络后端](https://www.encode.io/httpcore/network-backends/)。项目／智能体语义与 DeepSeek Harness 分析见 `webui-project-and-web-retrieval-review-20260908.md`；项目分组未改动。
