"""Reviewed capabilities that Agent drafts may reference by logical ID."""

from harness.studio.models import (
    AgentTemplate,
    BuiltinToolCapability,
    CapabilityCatalog,
    CapabilityRisk,
    ExecutionProfileMetadata,
    McpCapability,
    ModelRouteCapability,
    NetworkAccess,
    PolicyCapability,
    RuntimeCapability,
    TemplateCapability,
)


def default_capability_catalog() -> CapabilityCatalog:
    """Return the safe built-in catalog used until persistent catalogs are wired."""

    return CapabilityCatalog(
        modelRoutes=(
            ModelRouteCapability(
                routeId="deepseek-v4-flash",
                label="DeepSeek V4 Flash",
                provider="deepseek",
                models=("deepseek-v4-flash",),
                capabilities=("streaming", "tool_use"),
                credentialReference="NEW_API_KEY",
            ),
            ModelRouteCapability(
                routeId="deepseek-v4-pro",
                label="DeepSeek V4 Pro",
                provider="deepseek",
                models=("deepseek-v4-pro",),
                capabilities=("streaming", "tool_use"),
                credentialReference="NEW_API_KEY",
            ),
            ModelRouteCapability(
                routeId="minimax-m3",
                label="MiniMax M3",
                provider="minimax",
                models=("MiniMax-M3",),
                capabilities=("streaming", "tool_use", "vision"),
                modelType="vision",
                credentialReference="MINIMAX_M3_API_KEY",
            ),
            ModelRouteCapability(
                routeId="glm-5-3-flash",
                label="GLM-5.3-Flash",
                provider="glm",
                models=("glm-5.3-flash",),
                modelType="vision",
                capabilities=("streaming", "tool_use", "vision"),
            ),
        ),
        builtinTools=(
            BuiltinToolCapability(
                name="WebSearch", label="搜索公开网页",
                description="平台内置搜索，无需配置 MCP；仅发送公开关键词，结果带来源。",
                risk=CapabilityRisk.LOW, executionLocation="platform",
                approvalBehavior="公开关键词自动检索，疑似敏感信息拒绝外发",
            ),
            BuiltinToolCapability(
                name="WebFetch", label="读取公开网页",
                description="直接读取公开网页正文，拦截内网地址，限制跳转和内容大小。",
                risk=CapabilityRisk.LOW, executionLocation="platform",
                approvalBehavior="公开网页自动读取，不携带登录信息",
            ),
            BuiltinToolCapability(
                name="Read",
                label="读取文件",
                description="读取隔离工作区中的文件。",
                risk=CapabilityRisk.LOW,
                executionLocation="sandbox",
                approvalBehavior="自动允许",
            ),
            BuiltinToolCapability(
                name="Glob",
                label="查找文件",
                description="按文件名模式查找隔离工作区内容。",
                risk=CapabilityRisk.LOW,
                executionLocation="sandbox",
                approvalBehavior="自动允许",
            ),
            BuiltinToolCapability(
                name="Grep",
                label="搜索文件内容",
                description="在隔离工作区内检索文本。",
                risk=CapabilityRisk.LOW,
                executionLocation="sandbox",
                approvalBehavior="自动允许",
            ),
            BuiltinToolCapability(
                name="Write",
                label="创建文件",
                description="在隔离工作区中生成文件和交付物。",
                risk=CapabilityRisk.MEDIUM,
                executionLocation="sandbox",
                approvalBehavior="工作区内自动允许，越界写入拒绝",
            ),
            BuiltinToolCapability(
                name="Edit",
                label="编辑文件",
                description="修改隔离工作区中已有文件。",
                risk=CapabilityRisk.MEDIUM,
                executionLocation="sandbox",
                approvalBehavior="工作区内自动允许，越界写入拒绝",
            ),
            BuiltinToolCapability(
                name="Bash",
                label="运行命令",
                description="在隔离工作区中运行受策略约束的命令；安全只读命令自动放行。",
                risk=CapabilityRisk.HIGH,
                executionLocation="sandbox",
                approvalBehavior="安全命令自动允许，危险、越界或无法证明安全的命令拒绝或审批",
            ),
            BuiltinToolCapability(
                name="Task",
                label="委派子 Agent",
                description="把边界清晰的子任务委派给固定版本 Agent。",
                risk=CapabilityRisk.MEDIUM,
                executionLocation="runtime",
                approvalBehavior="受主/子 Agent 双重权限上限约束",
            ),
        ),
        mcpServers=(
            McpCapability(
                reference="tavily-readonly",
                serverName="tavily",
                label="公网搜索（Tavily）",
                description="检索和抽取公开网页，不提供网页写入能力。",
                endpointUrl="https://mcp.tavily.com/mcp/",
                tools=(
                    "mcp__tavily__tavily_search",
                    "mcp__tavily__tavily_extract",
                ),
                risk=CapabilityRisk.MEDIUM,
                networkAccess=NetworkAccess.EXTERNAL,
                sendsUserData=True,
                readOnly=True,
                executionLocation="external-mcp",
                credentialReference="TAVILY_API_KEY",
                authMode="bearer",
                authKey="api_key",
                version=2,
            ),
        ),
        policies=(
            PolicyCapability(
                policyId="production-read-only",
                label="生产只读",
                description="文件读取和审核过的只读 MCP；其他能力隐式拒绝。",
                risk=CapabilityRisk.LOW,
            ),
            PolicyCapability(
                policyId="production-standard",
                label="生产标准",
                description=(
                    "工作区写入及策略允许的命令自动执行；高风险、越界或不确定动作拒绝或确认。"
                ),
                risk=CapabilityRisk.MEDIUM,
            ),
            PolicyCapability(
                policyId="production-orchestrator",
                label="生产编排",
                description="允许固定版本子 Agent 委派，并继承各自权限上限。",
                risk=CapabilityRisk.MEDIUM,
            ),
        ),
        executionProfiles=(
            ExecutionProfileMetadata.model_validate(
                {
                    "profileId": "local-development",
                    "label": "本地开发 Preview",
                    "description": (
                        "仅用于显式启用 unsafe local sandbox 的单机开发与 Preview；"
                        "不提供进程、文件系统或网络强隔离，禁止用于生产发布。"
                    ),
                    "sandboxProvider": "local",
                    "networkAccess": (
                        NetworkAccess.NONE,
                        NetworkAccess.INTERNAL,
                        NetworkAccess.EXTERNAL,
                    ),
                    "risk": CapabilityRisk.HIGH,
                    "cpuMillis": 1000,
                    "memoryMiB": 2048,
                    "diskMiB": 10240,
                    "ttlSeconds": 3600,
                    "networkPolicyId": "unsafe-local-preview",
                    "allowedMcpReferences": ("tavily-readonly",),
                    "providerConfigReference": "local-unsafe-opt-in",
                    "productionAllowed": False,
                }
            ),
            ExecutionProfileMetadata.model_validate(
                {
                    "profileId": "isolated-default",
                    "label": "Docker 容器工作区",
                    "description": (
                        "在平台 Worker 的 Docker 容器工作区中执行文件、命令和工具；"
                        "保留租户、会话、产物和策略边界。"
                    ),
                    "sandboxProvider": "local",
                    "networkAccess": (
                        NetworkAccess.NONE,
                        NetworkAccess.INTERNAL,
                        NetworkAccess.EXTERNAL,
                    ),
                    "risk": CapabilityRisk.HIGH,
                    "cpuMillis": 2000,
                    "memoryMiB": 4096,
                    "diskMiB": 20480,
                    "ttlSeconds": 3600,
                    "networkPolicyId": "registered-mcp-only",
                    "allowedMcpReferences": ("tavily-readonly",),
                    "providerConfigReference": "docker-worker-local",
                    "productionAllowed": True,
                    "version": 2,
                }
            ),
            ExecutionProfileMetadata.model_validate(
                {
                    "profileId": "e2b-public-egress",
                    "label": "E2B 公网隔离执行",
                    "description": ("在 E2B 隔离微虚拟机中执行，允许访问审核过的公网模型与 MCP。"),
                    "sandboxProvider": "e2b",
                    "networkAccess": (
                        NetworkAccess.NONE,
                        NetworkAccess.EXTERNAL,
                    ),
                    "risk": CapabilityRisk.MEDIUM,
                    "cpuMillis": 2000,
                    "memoryMiB": 4096,
                    "diskMiB": 20480,
                    "ttlSeconds": 3600,
                    "networkPolicyId": "registered-public-mcp",
                    "allowedMcpReferences": ("tavily-readonly",),
                    "providerConfigReference": "e2b-managed",
                    "productionAllowed": True,
                }
            ),
            ExecutionProfileMetadata.model_validate(
                {
                    "profileId": "gvisor-production",
                    "label": "私有化 gVisor",
                    "description": "每个 Run 在 Kubernetes gVisor Pod 中强隔离执行。",
                    "sandboxProvider": "gvisor",
                    "networkAccess": (
                        NetworkAccess.NONE,
                        NetworkAccess.INTERNAL,
                        NetworkAccess.EXTERNAL,
                    ),
                    "risk": CapabilityRisk.MEDIUM,
                    "cpuMillis": 2000,
                    "memoryMiB": 4096,
                    "diskMiB": 20480,
                    "ttlSeconds": 3600,
                    "networkPolicyId": "registered-mcp-only",
                    "allowedMcpReferences": ("tavily-readonly",),
                    "providerConfigReference": "kubernetes-gvisor-managed",
                    "productionAllowed": True,
                }
            ),
        ),
        templates=(
            TemplateCapability(
                template=AgentTemplate.ANALYST,
                label="分析型",
                description="读取、检索、归纳和报告，默认最小只读权限。",
            ),
            TemplateCapability(
                template=AgentTemplate.OPERATOR,
                label="执行型",
                description=(
                    "在隔离工作区中生成或修改文件；常规操作自动完成，仅在高风险边界需要确认。"
                ),
            ),
            TemplateCapability(
                template=AgentTemplate.ORCHESTRATOR,
                label="编排型",
                description="将可独立验收的任务委派给固定版本子 Agent。",
            ),
        ),
        runtimeCapabilities=(
            RuntimeCapability(
                runtime="claude-agent-sdk",
                label="Claude Agent SDK",
                capabilities=(
                    "skills",
                    "builtin_tools",
                    "python_tools",
                    "mcp_http",
                    "mcp_sse",
                    "knowledge",
                    "subagents",
                    "tool_search",
                    "session_resume",
                    "approvals",
                    "artifacts",
                ),
                modelApiFormats=("anthropic_compatible", "openai_compatible"),
            ),
            RuntimeCapability(
                runtime="codex-app-server",
                label="Codex App Server",
                capabilities=(
                    "skills",
                    "builtin_tools",
                    "mcp_http",
                    "subagents",
                    "session_resume",
                    "approvals",
                    "artifacts",
                ),
                modelApiFormats=("openai_compatible",),
                limitations=(
                    "Studio Python tools are not connected",
                    "Knowledge references are not connected",
                    "On-demand tool search is not connected",
                    "Only streamable HTTP MCP registrations are supported",
                ),
            ),
        ),
    )
