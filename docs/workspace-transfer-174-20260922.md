# 工作区传输修复部署 174（2026-09-22）

分支 `fix/sandbox-workspace-transfer`（HEAD `67add773`，基于 `origin/develop`）。
目的：把"工作区回收从逐文件往返改为一次归档"这条修复放到 **174 的真实生产路径**（`cubesandbox` → `111`）上验证，
那里正是 09-21 那次 18MB 压缩包解成 4379 个文件、collect 撞 openresty 504 的现场。

## 部署方式（纯后端增量镜像）

`/data/agent-studio/builds/wt-67add773/`：

```
FROM harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:develop-20260921-3f58c360
COPY harness /app/project/lib/python3.12/site-packages/harness
```

- 源码用 `git archive --format=tar --prefix=src/ HEAD src/harness` 打（**不要**用 `tar -C` 直接打包目录：
  我第一版带上了 `__pycache__`，`src/harness` 从 318 个受控文件涨到 636，镜像里也带进了字节码，
  与 [[evolution-173-validation-stack]] 记的是同一个坑）。
- 新 tag `harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:wt-67add773`（已 push 到 Harbor）。
- 基座血缘已核对：`3f58c360`、`dd776bff`、`8e811cf5` 都是本分支的祖先，所以 `src/harness` 是**部署基座的超集**，
  没有降级风险。
- overlay `docker-compose/compose.deepagents-174.yaml` 只改 api/worker 两处 image（`sed` 前后都 `grep -n image:` 核对；
  web 那行不动）。备份 `compose.deepagents-174.yaml.bak-wt-67add773`。
- 重建命令与 2026-09-21 记录一致：
  `docker compose --env-file .env.production -p agent-studio-174 -f compose.yaml -f compose.harbor.yaml -f compose.deepagents-174.yaml up -d --no-deps --no-build --scale worker=3 --wait api worker`
- **部署前查过非终态 Run：全部终态**（cancelled 171 / failed 61 / rejected 7 / succeeded 655 / timed_out 5），
  没有打断正在执行的运行。

## 结果

| 项 | 值 |
|---|---|
| api-1 / worker-1..3 | `agent-studio-api:wt-67add773`，全部 healthy |
| web 3301 | 未动，仍是 `agent-studio-web:develop-20260921-3f58c360` |
| env 校验 | 86 个变量、关键 5 类齐全、`HARNESS_SANDBOX_PROVIDER=cubesandbox` |
| 容器内代码 | `harness/sandbox/base.py` 含 `extract_workspace_archive` |

**在部署环境内实测**（`docker exec` 进 api 容器，用容器自己的 `Settings()` 与 `/app/.venv/bin/python`
对真 cube@111 跑）：

| 路径 | 文件数 | collect | 本地落地 |
|---|---|---|---|
| provider 直连 | 800 | **0.74s** | **800/800** |
| deferred 包装（生产实际走的路径） | 800 | **0.67s** | **800/800** |

对照 09-21 的现场：同样量级在旧代码上是 collect 失败（openresty 502/504，只取回 249/800）。

## 回滚

```
cd /data/agent-studio/docker-compose
cp compose.deepagents-174.yaml.bak-wt-67add773 compose.deepagents-174.yaml
docker compose --env-file .env.production -p agent-studio-174 -f compose.yaml -f compose.harbor.yaml \
  -f compose.deepagents-174.yaml up -d --no-deps --no-build --scale worker=3 --wait api worker
```

## 未做

- **没有跑完整的业务 Run**（api → 队列 → worker → runtime → 工具门 → 沙箱）：`/v1/agents` 那条列举没返回可用 agent，
  时间用在这里性价比低，改为在容器内用部署代码 + 部署 env 直连 cube 验证（两条路径都验了）。
- 4500 文件级仍受平台限制（见 `docs/sandbox-workspace-transfer-20260922.md` §2），本次未再复测。
- 173 演化栈未部署；本分支未推送远端、未开 MR。
