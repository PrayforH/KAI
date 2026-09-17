# 沙箱治理设计与迭代计划（CubeSandbox 单后端路线）

日期：2026-09-17。代码基线：`feature/weknora-knowledge-base` 工作区 + 本次迭代改动。
本文同步写入飞书 `agent-studio` 目录。

## 0. 决策摘要

- **不考虑计量约束**。生命周期不再是全局公理，而是按执行配置（execution profile）声明的能力档位；Run 级正确性契约（provision/prepare/execute/collect/destroy + 逐 Run 工作区 + 产物回收）保留为不变基座。
- **CubeSandbox 是唯一生产后端**（用户决策）。Daytona/E2B 暂缓：适配器代码保留，不再投入。174 无外网，部署沿用"以现镜像为基座叠加 Python 文件"的发布模式。
- **111 上的沙箱平台问题只做暴露与联调，后续由独立运维负责**。问题清单见第 6 节。

## 1. 当前事实（截至本文写作）

### 1.1 平台侧（本仓库）

- 生产运行于 174：`HARNESS_SANDBOX_PROVIDER=cubesandbox` + `worker_cli_deferred`，3 个 Worker，模板 `tpl-f116a5f3d1c442b2b1690f4d`（nexau-code）。
- 适配器 `src/harness/sandbox/cubesandbox.py` 复用 E2B 协议栈：数据面用每实例 `Host: 49983-<id>.cube.app` 头经代理 IP 路由（零 DNS 依赖）；`validate_api_key=False` + 显式 Bearer 头过认证；CLI 从 Worker 离线上传二进制并校验版本（模板无 node/npm、且官方安装站被出口策略拦截，此路径是 remote_cli 可用的前提）。
- 治理挂点已存在且本轮已接线：`resolve_runtime_sandbox`（执行配置与实际后端不符即拒绝）与 `ContextTrust`（只升不降）。

### 1.2 沙箱平台侧（111，一手探测结论）

- 腾讯云 CubeSandbox v0.6.0 单机一键部署，自研 KVM microVM（宿主无 firecracker/kata/runsc 二进制；内核命令行 `console=hvc0`、`root=/dev/pmem0`、`clocksource=kvm-clock`）。单实例创建实测 0.267s，但**创建路径串行**（并发 10 墙钟 ≈ 10×单次，吞吐 ~3.9 个/s）。
- pause/resume、snapshot（提升为模板条目）实测可用；v0.7.0 的跨节点恢复为 preview，未部署。
- 网络出口：iptables TPROXY 将沙箱 80/443 全量重定向到 cube-egress（透明 MITM，CA 在 `/etc/cube/ca/`），白名单是运行时策略（`http://127.0.0.1:19090/v1/policies/dump`，当前为空 = 内置默认：放行 PyPI/APT/腾讯系，封禁模型 API/GitHub/npm）。DNS 由自带 CoreDNS 把 `*.cube.app` 解析到代理节点。
- **数据面鉴权实际未生效**：`secure=True` 下 SDK 拿到的 envd token 为 None，无凭据访问 envd `/health` 返回 204；配合 Host 头路由，**网络上任何能达 111:80 的主机知道 sandbox ID 即可调用该沙箱的文件/命令 API**。这是 111 移交清单的第一项。

## 2. 设计：两条正交的轴

### 2.1 轴一：生命周期阶梯（计量约束解除后放开）

| 档 | 机制 | 空闲成本 | 连续性 | 前置条件 | 状态 |
|---|---|---|---|---|---|
| L0 无沙箱 | deferred 模式纯模型轮不分配 | 0 | — | — | ✅ 已工作 |
| L1 Run 级销毁 | 现行默认 | 0 | 快照恢复 | — | ✅ 生产运行 |
| L2 会话常暖 | 确定性 ID + 空闲回收（DeerFlow 模式） | 中 | 已装环境存活 | 耐久租约（硬依赖） | 未接入 |
| L3 pause/resume | CubeSandbox 原生 | ≈0 | 进程/内存态可留 | 租约 + 单节点认知 | 未接入 |

档位由 execution profile 在**发布时声明**，运行时不做智能换档（保审计确定性）。选择依据是产品形态而非成本：交互对话用 L0/L1；重依赖代码/文档 agent 用 L2（或模板预装，见 2.3）；浏览器/GUI agent 用 L3。

**不变的底线**：多轮记忆永远不依赖沙箱存活（SessionStore + PostgreSQL + 对象存储快照恢复，`restore_session=True`）。这使任何常驻档位都只是可丢弃缓存。

### 2.2 轴二：信任分档与 enforcement 事实

三档模型：T0 受信（local + OS 原语强制，未建）/ T1 标准（CubeSandbox microVM，**现产默认**）/ T2 加固（K8s+gVisor + default-deny NetworkPolicy，留给 UNTRUSTED 内容）。CubeSandbox 以 T1 的运维成本提供接近 T2 的隔离边界，因此 T2 的差异化角色主要是"出口治理最强档"。

`ContextTrust`（SAFE/SENSITIVE/UNTRUSTED，只升不降）→ 影响本 Run 策略严格度 + 同 Session 下一 Run 的档位下限（后者为待办）。

### 2.3 本轮已落地的两个治理地基

**（a）enforcement 逐 Run 上报**（新增）

- `SandboxEnforcement = full | delegated | none`（`sandbox/base.py`）。推导规则只认 Provider 产生的事实：workspace 隔离 → none；kubernetes（gVisor Pod + default-deny）→ full；cubesandbox/daytona/e2b（含 `-deferred` 后缀）→ delegated；未知容器后端 fail-closed 到 none。
- 落点一：Run 事件流新增 `sandbox.provisioned`，payload 携带 `{provider, isolation, enforcement}`。
- 落点二：工具观测元数据（OTel/Langfuse）新增 `harness.sandbox.enforcement`，与既有 provider/isolation 并列。
- 后续（待办）：允许 execution profile 声明最低 enforcement，发布契约拒绝弱档。

**（b）跨副本会话序列化**（新增）

- `RedisSessionGate`（`storage/redis.py`）：Lua SET-NX + token 校验释放 + TTL 续期（TTL=可见性超时，续期=心跳间隔，与队列租约同拍）。Worker 死亡后门 ≤90s 自动过期，与 at-least-once 投递 + Run fencing 语义一致。
- `worker_loop` 的会话锁重构为可插拔 `SessionGate`：默认保持进程内行为（单进程部署不变），生产装配注入 Redis 门。此前"同 Session Run 串行"只在单进程内成立，多副本即竞争——本轮补齐。
- 新增双 worker_loop 共享队列的序列化测试（同 Session 互斥、异 Session 并行）。

## 3. 本次迭代实现与验证记录

改动文件：`sandbox/base.py`、`storage/redis.py`、`worker/main.py`、`worker/orchestrator.py`、`composition.py`、`api/dependencies.py`、`runtime/base.py`、`runtime/sdk_tool_gate.py`，新增测试 12 项（enforcement 推导 5、Redis 门 5、双 worker 2）。

本地：目标回归 188 passed；全量单测 1126 passed（2 个失败为存量 migration 断言过期，与本轮无关，stash 后复现确认）；Ruff 通过。

174 部署（镜像 `kai/axis-api:session-gate-20260917`，发布目录 `/data/session-gate-20260917`，含 rollback compose）：

1. 切换前活动任务为 0（runs 全部终态）；api + 3 worker 重建后 healthy，`/healthz` 200。
2. 冒烟 `smoke_cubesandbox.py`（延迟模式）1.62s 通过：创建、中文文件、产物校验、超时恢复、销毁。
3. **真任务级验证**（run_9b9663a1…，office-assistant 会话，经正式 API 入队）：
   - 事件序列出现 `sandbox.provisioned`，payload `{"provider":"cubesandbox-deferred","isolation":"container","enforcement":"delegated"}`；
   - 执行期间 Redis 存在 `harness:session-gate:local:<session>` 且跨轮询存活（TTL 续期工作），run 结束后正确释放、无残留；
   - 任务本身成功：Bash 工具执行 python3 写入 cube-gate-check.txt → `artifact.ready` → `workspace.archived` → `run.succeeded`；
   - 111 平台 `/health` sandboxes=0，无孤儿实例。

## 4. 后续迭代计划（优先级序）

| 序 | 事项 | 说明 |
|---|---|---|
| P0-1 | 111 数据面鉴权（移交运维，见第 6 节） | 网络侧临时缓解：限制 111:80 仅允许 174 网段 |
| P0-2 | execution profile 声明最低 enforcement + 发布门禁 | 让"可拒绝弱隔离"成为机制 |
| P0-3 | ContextTrust → 下一 Run 档位下限 | 接线已有，改动很小 |
| P1-1 | 耐久沙箱租约（DB 记录 tenant/session/run/owner/epoch/expires） | L2/L3 的硬前置；当前 Redis 门解决互斥，不解决所有权审计 |
| P1-2 | Profile 网络策略 → cube-egress 策略 API 下发与回读核验 | 111 的策略 API 已具备雏形（`/v1/policies`）；平台侧生成、核验、拒绝降级 |
| P1-3 | 实例治理：清单、指标、孤儿回收、模板/SDK 兼容检查 | 111 单点 + 串行创建（~4/s）是容量上限，突发会排队 |
| P1-4 | 多 Provider 路由（Profile 级选择） | 当前仍全局单选；已发布快照不可篡改 |
| P2-1 | 模板固化（预装 CLI 与依赖） | 已有先例（tpl-fb4a…）；比 L2 常暖更干净的环境演进路径 |
| P2-2 | L2 常暖 / L3 pause-resume 接入 | 依赖 P1-1 |
| P2-3 | Worker OS 原语强制（systemd ProtectSystem 等，DSH 做法） | 补 deferred 模式下 Worker 自身这层 |

明确不做：运行中智能换档；常驻作为全局默认（驻留时间与协调成熟度理由，非计量）；三套后端并行推进。

## 5. 参考与证据

- 本仓库：`docs/sandbox-selection-and-evolution-20260915.md`（选型）、`docs/cubesandbox-174-integration-20260915.md`（接入与迁移）、`docs/network-egress-review-20260906.md`（出口核查）。
- 一手探测（2026-09-15/16，本会话）：111 主机勘察（CubeMaster/cubelet/cube-shim/systemd 19 单元、TPROXY 规则、CoreDNS Corefile、mkcert 与 MITM 双 CA）、基准（单建 0.267s / 并发 10 串行）、E2B SDK 兼容与 `SSL_CERT_FILE` 真实校验通路。
- 外部：DeerFlow warm-pool 生命周期（三态 + 确定性 ID + ownership 租约事故修复）；OpenSandbox（Pool/BatchSandbox O(1) 批量、execd 独立鉴权、ingress TTL 续期）；DSH 同世界沙箱（Seatbelt/bwrap/Landlock、fail-closed）。OpenSandbox 的批量交付数字（100 个 0.92s）为二手来源，未实测。

## 6. 111 沙箱平台问题移交清单（运维）

> 按用户决策：111 上的问题当前仅用于暴露与联调，后续由独立运维负责。以下按风险排序。

1. **envd 数据面无鉴权（高）**。`secure=True` 未下发有效 token（SDK 侧为 None），无凭据可访问 envd API；Host 头路由使"可达 111:80 + 知道 sandbox ID"即等于控制该沙箱文件/命令面。建议：cube-proxy 层加 per-sandbox token 校验（参照 OpenSandbox execd 的 `X-EXECD-ACCESS-TOKEN`），或先做网络收敛（111:80 仅对 174 网段开放）。
2. **单节点 + 业务混部（中高）**。CubeSandbox 全部组件与 28 个其它业务容器同机；MITM CA 私钥（`/etc/cube/ca/cube-root-ca.key`）与数据面同机。扩容/搬迁应给独立节点，避免在混部节点上动内核与存储栈。
3. **创建路径串行（中）**。实测 ~3.9 个/s；批量/评测场景会排队。这是调度器实现问题，非硬件。
4. **出口白名单是平台级静态默认（中）**。`/v1/policies/dump` 为空 = 内置默认（放行 PyPI/APT/腾讯系，封禁模型 API/GitHub/npm）。平台侧要按租户/按 Run 下发策略需要 111 升级配合；模型网关域名当前未在白名单（remote_cli 模式跑模型调用前必须加）。
5. **证书与 DNS 形态（低，知悉即可）**。数据面证书为 mkcert 开发 CA（2028-12 到期）；`*.cube.app` 由自带 CoreDNS 解析（169.254.254.53），公网不解析。平台适配器已用 Host 头绕开两者，但直接访问（如浏览器预览）需配解析与信任 CA。
6. **探测残留**。模板列表中 `snap-9f4f12e8faaf4399b8189570`（2026-09-15，aliases 空）为探测期 `create_snapshot()` 产物，确认无用后可删。

## 7. 回滚

174 发布目录 `/data/session-gate-20260917/`：`compose.api.rollback.private.json`（切换前原样）。回滚步骤：确认无活动任务后 `docker compose -p agent-studio-174 -f compose.api.rollback.private.json up -d --no-deps --force-recreate --wait --scale worker=3 api worker`。本轮无数据库迁移，无状态回滚需求。
