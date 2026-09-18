# 174 按 develop 重建并开启推荐治理开关（2026-09-18）

## 1. 代码：运行中的部署逐文件等于 develop

先核验再重建，避免"以为部署了 develop"：

- 导出运行中镜像（当时的 `kai/axis-api:obslogs-20260918`）的 `harness/`，与本地 develop（`bfa9f8a`）的 `src/harness` 逐文件比 md5：**293/293 完全一致**；`migrations`、`alembic.ini` 也一致。
- 仍按"以基线为基座 + develop 全量文件集"重建**单层**镜像，链路里不再有中间补丁：

  | 项 | 值 |
  | --- | --- |
  | 镜像 | `kai/axis-api:develop-bfa9f8a`（`sha256:04e245a9…`） |
  | 基座 | `kai/axis-api:obs-egress-20260918c` |
  | 文件集 | 20 个 `harness/**.py` + `migrations/versions/0033_sandbox_leases.py` |
  | 发布目录 | `/data/develop-bfa9f8a/`（含 `compose.api.release.base.json`、`compose.api.release.private.json`、`compose.api.release.enforce.json`） |

- 镜像内哈希复核：`sandbox/deferred.py = d65285b6…`、`composition.py = e051937f…`、`sandbox/e2b.py = 213c928d…`，与本地 develop 逐个相等（对照：旧基座镜像三者都不同）。
- 已切换 api + 3 worker，全部 `healthy`；`/healthz` 200；容器内 `alembic_version = 0033`。
- 切换后在新镜像上复跑沙箱形态用例：B（延迟代理 microVM）成功并带 `lease_id/lease_epoch` 与信任字段；C 对照 `dns= 151.101.0.223`、实验（钉 `cubesandbox-egress-enforced`）`Temporary failure in name resolution`；`live leases: 0`；平台 `sandboxes: 0`。

## 2. 开启的治理开关（按推荐）

在 `compose.api.release.enforce.json` 中给 `api` + `worker` 注入两个环境变量后重建：

```
HARNESS_SANDBOX_TRUST_FLOOR_MODE=enforce
HARNESS_CUBESANDBOX_VALIDATE_TEMPLATE=true
```

- **模板校验**：启动期把模板 ID/别名解析成平台状态，`MISSING` 或非 `READY` 直接拒绝启动；平台不可达时返回 `None`（视为"无法核验"）**不阻塞启动**，因此不会因为平台抖动导致 api/worker 起不来。开启前已实测模板 `tpl-f116a5f3d1c442b2b1690f4d` 为 `READY`，重建后服务正常启动即为校验通过的证据。
- **信任下限强制**：Session 的信任水位一旦升高（不可逆），后端达不到该档直接拒绝 Run，而不是"记录后照跑"。

## 3. enforce 端到端验证（同一会话 A/B）

| 步骤 | 结果 |
| --- | --- |
| 建会话，第一轮（水位 `safe`） | `run_2382f597…` **succeeded** |
| 把该会话水位抬到 `untrusted` | DB `session_context_state.trust_high_watermark = untrusted` |
| 同一会话第二轮 | `run_183f4669…` **failed**，`error_type=SandboxGovernanceError`、`error_code=sandbox_governance`、消息 `sandbox provider is weaker than this Run requires: requires full enforcement, cubesandbox provides delegated`；**没有** `sandbox.provisioned`（拒绝发生在 provision 之前，错误形态不会被实际执行） |

## 4. ⚠️ 影响面（开启后实测，需要决策）

- 174 存量 `session_context_state` 共 192 行：`safe` 126、**`untrusted` 66**；按 `runs.updated_at` 看，其中有今天仍在活动的会话。
- 174 只有 CubeSandbox（`delegated`）一个后端，未配置任何 `full` 档后端（gvisor/k8s 未启用）。
- 因此：**任何读过不可信内容（例如联网抓取）的会话，其后续 Run 都会被直接拒绝**，直到水位回落（不会回落，ContextTrust 只会升高）或换到 full 档后端。

三个可选处置：

1. **切回 `report`**（一条命令，立即恢复）：去掉 `HARNESS_SANDBOX_TRUST_FLOOR_MODE` 后用 `compose.api.release.private.json` 重建 api+worker。违规事实仍会记录在 Run 事件的 `trust_floor` / `trust_floor_met` 字段里，只是不阻断。
2. **保持 `enforce`**，接受上表影响：适合把"弱隔离必须被拒绝"当作对外可见的安全行为来演示。
3. **补一个 full 档后端**：`HARNESS_SANDBOX_EXTRA_PROVIDERS=kubernetes` + k8s/egress 网关配置（174 目前没有），让高水位会话能被"升级"而不是被拒绝。

## 5. 回滚

```bash
cd /data/develop-bfa9f8a
# 仅回滚两个开关（镜像仍是 develop-bfa9f8a）
docker compose -p agent-studio-174 -f compose.api.release.private.json \
  up -d --no-deps --force-recreate --scale worker=3 api worker

# 回滚镜像（回到上一版）
docker compose -p agent-studio-174 -f compose.api.release.base.json \
  up -d --no-deps --force-recreate --scale worker=3 api worker
```

`0033` 只新增 `sandbox_leases` 表，旧镜像不读该表，回滚镜像无需回滚迁移。
