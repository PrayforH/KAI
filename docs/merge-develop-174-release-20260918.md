# 合并 develop 并发布到 174（2026-09-18）

## 1. 本次变更来源

- 分支：`feature/weknora-knowledge-base`，合并 `origin/develop` 的提交为 `11a22c3`（合并前基线 `59fcd5b feat(sandbox): govern CubeSandbox lifecycle, egress and observability`）。
- 合并冲突 9 个文件、其中 `composition.py` 16 处。冲突性质与处理：

| 文件 | 冲突性质 | 处理 |
|---|---|---|
| `sandbox/opensandbox.py`、`sandbox/cubesandbox.py`、`sandbox/e2b.py`、`config.py`、`api/dependencies.py`、`worker/orchestrator.py`、`tests/unit/test_production_composition.py`、`tests/unit/worker/test_orchestrator.py` | 相邻插入（双方在同一位置各加东西） | 都保留 |
| `composition.py` | **语义冲突**：双方各自实现了"按 execution profile 选后端" | 统一为 develop 的 `_sandbox_for_provider` + 本分支的 `_enabled_providers`/`select_sandbox_provider` 门禁 |

`composition.py` 统一后的设计：

- 唯一构造函数 `_sandbox_for_provider(settings, provider)`（develop 命名，`gvisor ↔ kubernetes` 翻译保留）。
- 启动期按 `HARNESS_SANDBOX_EXTRA_PROVIDERS` 逐个构建并校验（半配置的后端启动失败），得到 `sandbox_backends` / `sandbox_runtimes` 两张表。
- `select_sandbox_provider(pinned=..., enabled=..., default=...)`：profile 钉住的后端必须在本部署服务范围内，否则 `execution_profile_sandbox_provider_not_enabled`，不回落到默认。
- develop 的 `backends_for(provider_id)` 由"懒构建 + 缓存"改为查表（`sandbox_runtimes[provider_id], sandbox_backends[provider_id]`），避免同一后端被构建两次、也避免出现第二处配置来源。
- 两侧的 enforcement 判定都保留且语义不重叠：profile 声明的 `minimumEnforcement` 不满足 → 直接拒绝（`execution_profile_enforcement_below_minimum`）；Session 信任水位 → 按 `sandbox_trust_floor_mode`（report/enforce）。
- 出口策略作用域 `scoped()` 保留在本分支一侧；develop 的 `SandboxGovernanceService`（租约孤儿回收）与 `sandbox-orphans` 维护任务保留，本分支的按后端 `sandbox-expiry:<backend>` 保留，develop 的单默认后端 `sandbox-expiry` 因被覆盖而删除。

## 2. 本地验证

- `ruff check src` 通过（`tests` 下 4 处 E501 为 develop 既有，文件与 `origin/develop` 完全一致）。
- `pytest tests/unit`：**1274 passed**。

## 3. 镜像与发布目录

- 旧镜像（回滚用）：`kai/axis-api:obs-egress-20260918c`（api + 3 worker）
- 新镜像：`kai/axis-api:merge-develop-20260918`（`sha256:ac2fa4b7fd78…`）
- 构建方式：沿用"以现镜像为基座叠加文件"的补丁构建（174 无外网）。
  - `FROM kai/axis-api:obs-egress-20260918c`
  - `COPY` 19 个 `harness/**.py` 到 `/app/project/lib/python3.12/site-packages/harness/`
  - `COPY migrations/versions/0033_sandbox_leases.py` 到 `/app/migrations/versions/`
- 文件集由"基线镜像内的 `harness/` 与实际工作树 diff"得出，不是按提交猜测：
  - 改动 14：`api/dependencies.py`、`composition.py`、`config.py`、`core/errors.py`、`governance/api.py`、`sandbox/{base,cubesandbox,daytona,e2b,kubernetes,opensandbox}.py`、`storage/models.py`、`worker/{main,orchestrator}.py`
  - 新增 5：`sandbox/{claude_cli,governance,lease,opensandbox_session}.py`、`storage/sandbox_lease_repository.py`
  - 迁移 1：`0033_sandbox_leases.py`
- 依赖：develop 新增 `websockets>=15,<17`，基线镜像已随 `uvicorn[standard]` 装有 16.1，无需离线装包。
- 发布目录：`/data/merge-develop-20260918/`（`build/`、`compose.api.release.private.json`、`compose.api.release.base.json`）。

## 4. 发布步骤

1. 切换前活动任务 = 0（`runs` 非终态计数为 0）。
2. 迁移（表 `sandbox_leases`）：

   ```bash
   cd /data/merge-develop-20260918
   docker compose -p agent-studio-174 -f compose.api.release.private.json \
     run --rm --no-deps --workdir /app migrate
   # → Running upgrade 0032 -> 0033, Durable sandbox leases.
   ```

   **注意**：`migrate` 服务在补丁镜像里必须显式 `--workdir /app`。compose 里该服务只写了 `command: ["alembic","upgrade","head"]`，依赖镜像的 `WORKDIR`；补丁镜像的 WORKDIR 是 `/`（原始 docker 镜像才是 `/app`），直接 `up -d migrate` 会报 `No 'script_location' key found in configuration`。

3. 切流：

   ```bash
   docker compose -p agent-studio-174 -f compose.api.release.private.json \
     up -d --no-deps --force-recreate --scale worker=3 api worker
   ```

4. 结果：`api` + `worker-1/2/3` 均为 `kai/axis-api:merge-develop-20260918` 且 `healthy`；`quality-sync`、`web` 未动。

## 5. 验证证据

- `GET /healthz` → 200 `{"status":"ok"}`（宿主 `http://127.0.0.1:8800/healthz`）。
- **真任务**（正式 API 入队，非 smoke 脚本）：
  - session `session_99d587abed5d4f5799739b9b112628df`（agent `egress-gray-probe` 0.1.0，`environment=test` → 快照 `deployment_snapshot_64408cd5…`，钉在 `cubesandbox-egress-enforced`）
  - run `run_b8ad0e0df9684e2e87f25efa7fdc5ded`，20s 内 `run.succeeded`，事件序列 `run.queued → run.provisioning → sandbox.provisioned → run.running → tool.allowed → tool.result → workspace.archived → run.succeeded`
  - `sandbox.provisioned` payload（develop 的信任字段与本分支的租约字段在同一事件里）：

    ```json
    {"provider":"cubesandbox-deferred","isolation":"container","enforcement":"delegated",
     "trust_watermark":"safe","trust_floor":"none","trust_floor_met":true,
     "lease_id":"sandbox_lease_0f2725545e35449ba77e4dadd99d8af9","lease_epoch":1}
    ```

  - `sandbox_leases` 行存在且 `state=released`（10:48:23 建，10:48:29 释放，owner `run-fence:1`）。
  - CubeSandbox 平台 `/health` → `{"status":"ok","sandboxes":0}`，无孤儿实例。
  - worker 日志无 error/exception。
- **租户目录未被本次发布触碰**：`capability_catalogs`（tenant `local`）仍为 revision 75、`updated_at` 10:18:38（模型配置导入那次），`sentiment_query_mcp` 条目原样保留。

## 6. 回滚

```bash
# 1) 确认无活动任务
# 2) 切回旧镜像
cd /data/merge-develop-20260918
docker compose -p agent-studio-174 -f compose.api.release.base.json \
  up -d --no-deps --force-recreate --scale worker=3 api worker
# 3) 如需回滚迁移（0033 只加表，向后兼容，可保留）
docker compose -p agent-studio-174 -f compose.api.release.base.json \
  run --rm --no-deps --workdir /app migrate  # 需先把 command 换成 alembic downgrade 0032
```

`0033` 只新增 `sandbox_leases` 表与索引，旧镜像不读该表，因此**只回滚镜像、不回滚迁移**是安全的。

## 7. 遗留与后续

1. `migrate` 服务缺少 `working_dir: /app`：补丁镜像模式下必须手工加 `--workdir`；建议在 `deploy/docker-compose/compose.yaml` 的 `migrate` 服务上显式写 `working_dir: /app`，或让 Dockerfile 设置 `WORKDIR /app`。
2. 出口策略（`sandbox_egress_enforcement=enforced` / profile `egressEnforcement=enforced`）仍只在 `worker_cli_deferred` 模式下生效：`resolve_runtime_sandbox` 在非延迟模式提前 return，`scoped()` 不会执行。本轮合并保持原行为，未扩大范围。
3. 本次验证产生的测试对象：session `session_99d587abed5d4f5799739b9b112628df`、run `run_b8ad0e0df9684e2e87f25efa7fdc5ded`，以及此前灰度留下的探针 Agent `egress-gray-probe` / `test` 快照 / 目录绑定，待确认后清理。
