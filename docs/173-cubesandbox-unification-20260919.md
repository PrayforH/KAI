# 173 沙箱后端统一为 CubeSandbox（2026-09-19）

## 1. 变更

173 此前因"174 不能动"的权宜安排使用 OpenSandbox@115；按用户决策统一为 **CubeSandbox@111**，
与 174 相同。跨主机 worker（174 主机的 `axis-worker-for-173`）同步切换。

改动点（全部在 173 的 `/data/agent-studio/docker-compose/`）：

1. `.env.production`（备份 `.env.production.bak-cube-*`）：
   - `HARNESS_SANDBOX_PROVIDER=opensandbox → cubesandbox`
   - 追加 `HARNESS_CUBESANDBOX_{API_URL,PROXY_URL,TEMPLATE,DOMAIN,CLAUDE_CLI_VERSION,VALIDATE_TEMPLATE,API_KEY}`
     （取值与 174 完全一致：API `111:13000`、代理 `111:80`、模板 `tpl-f116a5f3…`、CLI `2.1.259`、
     模板校验 `true`）
2. `compose.develop-bfa9f8a.yaml` 的 `x-sandbox-environment` 锚点追加 7 个
   `HARNESS_CUBESANDBOX_*` 转发（此前只转发 opensandbox 组，cube 变量到不了容器）。
3. `api` + `worker×3` 重建；174 上的远程 worker 更新 env 后重建（`--restart unless-stopped` 保持）。
   OpenSandbox 的 env 保留未删（provider 已不指向它，回滚时可直接切回）。

前置核验：173→111 的 `13000`（控制面）与 `80`（数据面代理）均可达。

## 2. 验证

| 项 | 结果 |
| --- | --- |
| 模板校验（`VALIDATE_TEMPLATE=true`，启动期） | api/worker 全部 healthy = 模板 `READY` 校验通过 |
| 真任务（经 173 API） | run `run_3e1923fe…` succeeded |
| `sandbox.provisioned` | `{provider: cubesandbox-deferred, isolation: container, enforcement: delegated, lease_id, trust_watermark: safe}` |
| 租约 | released |
| 认领者 | **174 的远程 worker**（日志 22 处该 run 记录，驱动 `111:13000`）——跨主机多 worker 在混合运行下自然分摊的实测样本 |
| 111 平台状态 | `{"status":"ok","sandboxes":0}`，无孤儿 |

两套环境现在同为 CubeSandbox@111 + worker_cli_deferred + report：

| | 174 | 173 |
| --- | --- | --- |
| API/Worker | `develop-bfa9f8a` ×(1+3) | `develop-bfa9f8a` ×(1+3+1 远程) |
| 沙箱 | cubesandbox @111 | cubesandbox @111 |

## 3. 回滚

```bash
# 173 切回 OpenSandbox@115
cd /data/agent-studio/docker-compose
cp .env.production.bak-cube-<ts> .env.production
# overlay 里的 cube 转发行可保留（provider 不指向 cube 即不生效）
docker compose --env-file .env.production -f compose.yaml -f compose.harbor.yaml \
  -f compose.codex-runtime.yaml -f compose.develop-bfa9f8a.yaml \
  up -d --no-deps --force-recreate --scale worker=3 api worker
# 174 远程 worker 同理：把 /data/worker-for-173.env 的 PROVIDER 改回 opensandbox 后重建容器
```
