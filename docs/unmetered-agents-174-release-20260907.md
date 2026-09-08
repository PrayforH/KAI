# 全平台取消运营额度限制 · 20260907-115304

正式环境：http://172.20.109.174:3501

## 根因

最新失败任务「涉非风险企业增量分析报告」，Run `run_0b7b67acc8204f599f6f1969e8313fa1`，北京时间 2026-09-07 11:29:40 开始，11:37:57 失败。

- 事件 170 的 `runtime.result` 为 `error_max_budget_usd`，SDK 记录费用 4.005826 美元、28 回合。public-opinion-agent@0.3.23 发布快照限制为 4 美元。该费用是 SDK 计量，不等同于核验后的供应商账单。并非 Worker 崩溃或运行超时。
- 两次 Agent 调用的 subagent_type 分别为 general-purpose / claude；事件 55/62 的 policy_denied 原因是角色未在发布 Manifest 声明，子智能体实际没有启动。
- 已发布角色为 fact-researcher、audience-analyst、industry-analyst，均绑定 helper-agent@1.0.0，仅有 Read/Glob/Grep；不能承担本次所委派的 MCP 全量拉取、写文件或脚本计算。
- 前端把 Agent 请求归入子任务，但未把 tool.result 合并回子任务，造成拒绝后仍显示运行中。预算超限也被收口为笼统的模型失败。

## 用户要求与变更

适用于整个 agent-studio、全部主智能体/子智能体、现有历史版本、后续新建/导入/发布版本。费用和 Token 仅作观察，不作为执行额度。

- Claude 本地和远程传输均不传 max-budget-usd；历史 Manifest 无须改写。
- 环境配置不再给 Run 加费用/Token 额度；QuotaService 不做模型费用或 Token 预留、准入拦截，即便旧租户/智能体策略仍有额度也不会生效。用量事件及观察信息保留。
- 子任务 Token 使用量不触发 SubagentGovernanceError。
- DraftLimits 接收新建、导入和编辑数据时将三个旧运营额度字段归一化为 null；编译器发布 Manifest 不输出这些字段。
- 管理界面移除费用/Token 额度编辑；超时、并发、权限与文件隔离等保护保留。
- 主智能体提示根据真实发布版本列出准确角色名称和各自工具，不假定子智能体继承父级 MCP/写入能力；拒绝未声明角色时返回可用名称。
- tool.result 收束未启动子任务的状态；历史预算失败显示具体原因。

历史失败状态和工作区快照 `snapshot_bcb707901da74d8193fdd35c0c85ebeb` 保留。未重新执行用户业务，也未把差集初算当作最终报告。

## 验证

- 后端主要回归 148 项通过；扩展回归 109 项通过（两组有重叠，不合并计数）。覆盖运行配置、远程 CLI、配额、子任务治理、Studio 编译、导入发布和 Worker。
- 扩展检查发现既有目录 API 测试漏写已存在的 skills 字段，仅补齐测试期望，未改业务接口。
- 前端 74 个文件 / 489 项通过；本地生产构建通过；Ruff、Pyright 和 git diff --check 通过。
- 在 174 使用现有离线依赖镜像构建，lockfile 与上一发布 SHA256 相同，无依赖升级。API 仅覆盖本次 9 个文件，与当前运行基线逐文件比较。
- API、3 个 Worker、quality-sync、Web 均 healthy / restart 0。首页、鉴权配置、API healthz 返回 200。
- 使用现有服务认证只读获取真实失败 Run 的 169 条活动投影，验证预算原因；前端 SSR 回放断言两个子任务都为 failed，并在 Chrome 本地回放页验证可见文案。该回放不是新模型任务。验证页已关闭。
- 原网页登录会话到期，用户需重新登录；未生成用户登录凭据或更改认证配置。

## 部署和回滚

源码包：`/data/kai-unmetered-20260907-115304`。
API/Worker/quality-sync 镜像：`kai/axis-api:20260907-115304`，ID `sha256:0a0f1ea1082a53c39dddcfb5e610e495d03d7345e963d2de6ea055945e31b5b8`。
Web 镜像：`kai/axis-web:20260907-115304`，ID `sha256:21ab15b4b3caf017745ce6f2830b5608314ab8ed0b66990aa48a67d3165d791a`。
Web 容器：`axis-web-20260907-115304`，3501，restart unless-stopped。

后端 Compose 在原 compose.yaml、compose.harbor.yaml、compose.axis-release-20260906-132229.yaml 之上追加 compose.axis-unmetered-20260907-115304.yaml。部署前活跃任务为 0，配置继承原环境，无数据库迁移或发布快照修改。

回滚会恢复旧的预算执行行为。回滚前确认没有活跃任务：

```sh
docker stop axis-web-20260907-115304
docker start axis-web-20260906-144300
cd /data/agent-studio/docker-compose
docker compose --profile observability --env-file .env.production \
  -p agent-studio-174 -f compose.yaml -f compose.harbor.yaml \
  -f compose.axis-release-20260906-132229.yaml \
  up -d --no-deps --no-build --scale worker=3 api worker quality-sync
```

回滚后检查首页、healthz 和各容器健康。原 3301 Web、数据库、对象存储及用户任务数据保持原状。
