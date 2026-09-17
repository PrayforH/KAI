# 沙箱治理在 173 的发布与验证记录（2026-09-18）

本轮把飞书《沙箱治理设计与迭代计划》里的 P0-3 / P1-1 / P1-3 落到代码，并按用户要求
**在 173 上验证**（174 同时在跑别的发布）。173 的沙箱后端切到 **115 的 OpenSandbox**，
因为 174 不能动、而 173 的 worker 可以直连 115:8090（实测 200）。

## 1. 发布内容

代码基线 `develop`：`9ac0ed9`（三件套）→ `a0b35a0`（无租约残留回收）→
`696fe16`（按 Run 元数据判定归属）→ `2c3ce6e`（信任水位读取用会话自身用户）→
`de210c6`（拒绝原因进事件）。

- **P0-3 信任下限**：`ContextTrust` 只升不降 ⇒ 同一 Session 的后续 Run 不得低于对应
  enforcement 档（untrusted ⇒ full）。`HARNESS_SANDBOX_TRUST_FLOOR_MODE` 取
  `report`（默认，事件里报告差额）或 `enforce`（直接拒绝 Run）。
- **P1-1 耐久租约**：`sandbox_leases`（迁移 0033）记录 tenant/session/run/owner/epoch/
  provider/sandbox_id/expires；服务提供 acquire / renew（worker 心跳）/ release /
  expired；epoch 每次获取自增，供失去所有权的 worker 自检。
- **P1-3 实例治理**：把活租约清单与平台实例清单对账，回收“无主”沙箱；
  `GET /v1/studio/governance/sandbox-instances` 暴露给运维，同时作为
  `MaintenanceReaper("sandbox-orphans")` 周期运行。

## 2. 173 部署方式（叠加发布 + 临时 override）

主机构建从 Harbor 走不通（docker build 在下载 Node 一步失败：镜源网络），因此沿用仓库
在 174 已验证的**叠加发布**方式：

- 发布目录 `/data/agent-studio-governance/`：`Dockerfile`
  （`FROM harbor…/agent-studio-api:develop-20260916-cf97d79` + `COPY files/harness`
  + `COPY files/migrations` + 清 `__pycache__`）、`files/{harness,migrations}`、
  `.env.production.bak`。
- 镜像 `kai/axis-api:governance-20260918`（本机构建，未推 Harbor）。
- 临时 override `/data/agent-studio/docker-compose/compose.governance.yaml`：把
  api/worker/migrate 指向该镜像，并转发主机 compose 副本里还没有的沙箱环境变量。
- `.env.production` 变更：`SANDBOX_PROVIDER=opensandbox`、
  `SANDBOX_EXECUTION_MODE=worker_cli_deferred`、`SANDBOX_TRUST_FLOOR_MODE=enforce`、
  `OPENSANDBOX_API_URL/API_KEY/IMAGE`。**未改动** `HARBOR_IMAGE_TAG`（web 保持原镜像）。
- 迁移 0033 已由 migrate 容器应用（`sandbox_leases` 存在）。

回滚：`cp .env.production.bak .env.production` + 删除 `compose.governance.yaml`，再
`docker compose --env-file .env.production -f compose.yaml -f compose.harbor.yaml
-f compose.codex-runtime.yaml up -d --wait`。

## 3. 验证结果（173 + 115 OpenSandbox）

| 项 | 证据 |
|---|---|
| 真实任务走新后端 | run `run_65f44d4c…` succeeded；`sandbox.provisioned` = `{provider: opensandbox-deferred, isolation: container, enforcement: delegated, trust_watermark: safe, trust_floor: none, trust_floor_met: true, lease_id, lease_epoch: 1}` |
| 租约生命周期 | 同一 run 产生 1 行 `sandbox_leases`，owner `run-fence:1`，run 结束后 state=released、`released_at` 非空 |
| 会话内多轮 | 同一 thread 第二轮落在同一 session（`session_53cdd181…`），未新建会话 |
| 信任下限拒绝 | 用平台自身 `ContextService.promote_trust` 把该会话提到 untrusted 后，下一轮 run **failed**，事件 `error_code=sandbox_governance`、message 明确写出“session requires full enforcement, opensandbox provides delegated”；**未创建任何 lease、未创建任何沙箱** |
| 治理清单接口 | `GET /v1/studio/governance/sandbox-instances` 返回租约清单（API 进程没有平台客户端，故 `platform_instances: null` 并如实报告 provider；平台侧对账由 worker 的 reaper 与指标 `harness_sandbox_live_leases` / `harness_sandbox_platform_instances` 提供） |

## 4. 验证过程中发现并修掉的真实缺陷

1. **归属按沙箱 id 比对是错的**：deferred provider 交给编排器的是本地 handle id，与平台
   沙箱 id 永不相等 ⇒ 每个运行中的沙箱都会被判成“无主”，宽限期一过回收器就会销毁**活任务**
   的沙箱。改为按 provider 写入的 `harness.run` 元数据判定归属。
2. **信任水位读错了身份**：上下文状态按 Session 的 user 存储，而新检查传的是 agent owner
   ⇒ 永远读回空状态（safe），门禁形同虚设。改为 `session.user_id`。
3. **无租约的残留永远不会被回收**：回收器只处理“有过租约但已过期”的实例，探针或创建后即
   崩溃留下的沙箱会一直泄漏。现在结合平台创建时间，超过一个完整租约 TTL 才回收，无创建
   时间则只报告不销毁。
4. **回收列表写成了布尔值**：`reclaimed` 记录的是销毁返回值而不是 sandbox id（单测抓到）。
5. **拒绝原因不可诊断**：原先只报 `runtime_error`，运维看不出为什么。现在抛
   `SandboxGovernanceError`，事件带 message 与 `error_code=sandbox_governance`。

## 5. 计划中仍未完成（继续迭代）

| 序 | 事项 | 现状 |
|---|---|---|
| P1-4 | Profile 级多 Provider 路由 | 未做：仍是全局单后端；已发布快照不可篡改的要求需要在发布契约里落 |
| P1-2 | Profile 网络策略 → 平台策略下发与回读核验 | 未做：OpenSandbox 创建接口有 `networkPolicy`，111 侧有 `/v1/policies` |
| P2-1 | 模板固化（预装 CLI 与依赖） | 未做：本轮实测单次 259MB CLI 上传 82s，是 remote_cli 的主要开销 |
| P2-2 | L2 常暖 / L3 pause-resume | 未做：依赖 P1-1（租约已就绪）与平台 pause/resume |
| P2-3 | Worker OS 原语强制（deferred 模式） | 未做：compose 层系统调用/能力收敛 |
| P0-1 | 111 数据面鉴权 | 运维事项（网络收敛），本仓库只暴露问题 |

已知环境提示：173 的 `kai/axis-api:governance-20260918` 是本地镜像，主机上没有
`/data/agent-studio-governance/` 之外的构建记录；后续若从 Harbor 正式发布，需要先解决
本机构建在 Node 下载步骤的网络问题。
