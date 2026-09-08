# 联网策略核查与建议 · 2026-09-06

结论：建议采用 **② 受控出口代理与域名白名单 + ④ MCP 能力治理**。②约束进程能连哪里，④约束谁能调用什么、使用何种凭据和如何审计。当前代码具备部分基础，但不能把设计目标写成“全项目已经默认断网”。本次只分析，不变更网络权限或生产执行后端。

## 174 的实际状态

只读检查正在服务的三个 Worker，均为 `sandbox_provider=local`、`sandbox_execution_mode=remote_cli`、`allow_unsafe_local_sandbox=true`；运行时配置为 `claude-sdk`。`remote_cli` 是执行模式名称，不代表当前部署用了远程微虚拟机。

三个 Worker 都在普通 Docker bridge 网络中，`privileged=false`，网络 `internal=false`；检查的 `DOCKER-USER` 链只有 RETURN。Worker 没有设置 HTTP_PROXY/HTTPS_PROXY。虽然 Settings 中 Kubernetes proxy URL 非空，当前 local 分支并不使用它。Codex 网络开关为 false、E2B 联网开关为 true，但它们不是当前 Claude/local 路径的网络隔离措施。

这些证据说明：**项目没有在这个执行路径上实施按 Run 的默认拒绝出口策略**。不等于所有公网均可到达：宿主机其他防火墙、公司网络和上游访问控制仍可能阻止连接；本次没有将未核查的上游网络当作全开放，也没有进行公网扫描。之前构建下载被拒绝，同样不能证明本项目实现了完整的出口治理。

## 对原分析的修正

| 原说法 | 当前证据与准确表述 |
| --- | --- |
| 模型和 Bash 默认不能联网 | 不能泛化。模型请求本来需要访问模型服务；local 子进程共享 Worker 网络。业务工具权限与控制面联网应分别描述。 |
| 独立 Docker 沙箱后端 | Settings 只有 local/daytona/e2b/kubernetes。`isolated-default` 标签叫 Docker 容器工作区，实际 `sandboxProvider=local`；LocalSandboxProvider 用临时目录和子进程，不会每 Run 创建容器或网络命名空间。Worker 自身在 Docker 中，不等于任务间具备容器隔离。 |
| Bash 白名单会挡住所有联网命令 | 低风险判定仅在工具原判定 ASK 时用于自动放行。当前 standard/orchestrator 策略含 `sandbox-bash: ALLOW`，因此不能用低风险列表没有 curl 来证明 curl 被拒绝。命令审核仍不能替代防火墙。 |
| Profile 发布时锁死网络权限 | 编译器校验 MCP 网络等级与 Profile 兼容性，运行时也检查 provider 匹配；但声明 `registered-mcp-only` 没有自动为 local/E2B/Daytona 下发对应域名级防火墙。契约检查已存在，所有后端的一致强制执行还没有。 |
| E2B secure=True 就是网络隔离 | secure 是 Envd 访问令牌保护，与出口不同。当前适配器只传 allow_internet_access 布尔值，默认 true，没有映射每个 Run 的域名策略。 |
| Kubernetes 全部拒绝出口 | 实现是默认拒绝后显式放行集群 DNS 和代理 TCP 3128；HTTP(S) 代理环境变量与 NetworkPolicy 一起使用。域名约束在 Squid 配置中，需 CNI 实际支持 NetworkPolicy。 |
| Daytona 只能等待平台完善 | Daytona 当前官方文档已有域名/IP 白名单、禁网与出口代理能力。174 安装的 Python SDK 0.196.0 已暴露 network_block_all/network_allow_list/domain_allow_list；但本项目创建参数未接入这些字段，该版本模型也未暴露 outbound proxy 字段。应核对 SDK 与自托管服务版本，不能只升级客户端便宣称可用。 |
| 只有非只读 external MCP 才回退 remote_cli | `_manifests_require_remote_cli` 判定的是 python_entry 或任何不在只读引用集合中的 MCP，并未只限定 external。 |
| public-opinion-agent 使用 Tavily + production-read-only | 当前仓库 YAML 使用 sentiment_query_mcp + production-orchestrator，声明 Codex runtime。研究智能体仍使用 Tavily + knowledge-search + production-read-only。仓库模板不等于每个用户在数据库里的已发布版本。 |
| 其他没有 MCP 的智能体“完全断网” | 能得出的结论只是“未声明业务 MCP”；Read/Bash/运行时宿主网络是否隔离要分别验证，不能仅从 Manifest 推导。 |

保留正确部分：MCP 注册的 none/internal/external 分级、地址检查与 external HTTPS 限制、发布时兼容检查、外部结果不可信标记和上下文信任水位、平台 Memory/Knowledge 的认证 HTTP MCP 回调，都是有用的治理基础。信任降级影响后续策略，但不会自动切断网络。MCP 协议本身也不会自动提供身份和审核，必须由平台落实。

## 建议的职责分工

| 通道 | 推荐实现 |
| --- | --- |
| 模型调用 | 固定模型网关，短期、按 Run 限额的凭据；生产 API key 尽量由网关注入，不交给可执行代码。模型服务连通与业务公网访问分开配置。 |
| 搜索、网页抓取、舆情数据库、知识库、写入业务系统 | 使用注册 MCP；按身份、工具、读写级别授权，密钥留在服务端，结果标注不可信，审计保留必要元数据。允许公网抓取的 MCP 服务自身也要做地址、重定向、下载大小和出口控制。 |
| Bash/Python、文件处理 | 任务执行网络默认拒绝，只允许必须的平台代理与回调；拒绝直接公网/原始 IP 绕过。仅设置 HTTP_PROXY 不够。 |
| 包安装、Git、数据下载 | 有明确需求才提供受控下载出口，使用内部镜像/预装依赖优先。没有必要把每个下载命令都重新包装为业务 MCP，但也不应因此开放任意网络。 |
| Memory/Knowledge/产物上传等平台回调 | 独立平台服务白名单，Run 绑定令牌和短有效期；不要为了可用性笼统开放整个私网。 |

保留 MCP 的网络分类，同时增加独立的执行出口策略，包含允许服务/域名、端口、策略版本及后端执行能力。`none` 应解释为“无业务外网访问”，明确列出模型与平台控制通道，避免“断网但仍在调用模型”的语义冲突。

## 落地顺序

1. **先建立真实执行边界**：174 当前 local 路径若保留多租户代码执行，优先迁移到现有 Kubernetes/gVisor 分支或受支持的独立沙箱；Worker 的 Docker 外壳不等同于 Run 隔离。不要仅切换设置就上线，先验证已有任务、文件恢复、MCP 和模型回调。
2. **统一出口契约**：发布时生成可执行策略，启动前由后端应用并核验。平台不支持该策略时拒绝启动，禁止悄悄降级。池化/恢复的沙箱必须重新校验租户和策略版本。
3. **接通②与④**：Kubernetes 已有 DNS/代理 NetworkPolicy + Squid 白名单可扩展；Daytona 接入 SDK 已支持的字段并验证服务端能力；E2B 将布尔开关升级为网络策略映射。Worker 到 MCP、MCP 到外部的出口也纳入约束，避免只封住沙箱这一跳。
4. **修正默认代理模板**：当前 Helm 默认仅允许 `.anthropic.com` 与 `.mcp.tavily.com`，不能直接满足已有模型网关、舆情服务和平台回调。按实际服务生成目的地，限制安全端口，防止白名单域名解析到非预期私网/元数据地址；代理规则不能对所有租户共用无限并集。
5. **独立验收后灰度**：允许的模型、MCP、回调可用；未允许域名、原始 IP、IPv6、直连与非代理端口均被拒绝；验证重定向/DNS 重绑定、代理不可用时不绕过、并发任务策略不串用、恢复旧沙箱不保留旧权限。失败应有清晰原因，不能表现为长期“正在处理”。

这项改造的优先级应高于“再增加一个联网工具”。先落实出口和任务隔离，再扩展联网能力，既保留联网智能体的实用性，也能让产品中的权限说明有实际执行依据。

## 代码依据

- `src/harness/config.py`：后端枚举及联网默认值。
- `src/harness/composition.py`：`_sandbox`、`_manifests_require_remote_cli`、`resolve_runtime_sandbox`。
- `src/harness/studio/catalog.py`：isolated-default 元数据；`compiler.py`：网络级别与 Profile 检查。
- `src/harness/sandbox/local.py`、`daytona.py`、`e2b.py`、`kubernetes.py`：实际执行与网络参数。
- `deploy/helm/agent-harness/templates/egress-proxy.yaml`、`values.yaml`：Squid 默认规则。
- `src/harness/runtime/sdk_tool_gate.py`、`src/harness/policy/rules.py`、`bash_safety.py`：命令审核实际顺序。
- `agents/public-opinion-agent/agent.yaml`、`agents/networked-knowledge-research-agent/agent.yaml`：当前模板。

## 官方资料

- [Daytona Network Limits](https://www.daytona.io/docs/en/network-limits/)：当前沙箱网络字段、创建及更新规则，组织策略仍可能进一步限制。
- [E2B Python SDK](https://e2b.dev/docs/sdk-reference/python-sdk/v2.9.1/sandbox_async)：secure 与联网开关不同，存在独立 network 配置。
- [Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)：需网络插件支持，策略作用范围与显式允许行为。

以上为 2026-09-06 对工作区代码与 174 运行配置的核查，不将其他分支、旧文档或供应商新特性当作当前部署已具备能力。
