# auto/agent-evolution 部署到 173 隔离验证环境（2026-09-22）

把 `auto/agent-evolution` 的当前 HEAD `0f6149cb` 构建并部署到 173 的隔离验证栈
（compose project `agent-evolution-173`，API `8802`、Web `3302`）。原有 `8800/3301`
生产栈未改动。

## 1. 部署内容

| 组件 | 部署前 | 部署后 |
| --- | --- | --- |
| api / worker | `kai/axis-api:evolution-webtools-948e9b6f` | `kai/axis-api:evolution-0f6149cb`（`0f852175d8b8`） |
| web | `kai/axis-web:evolution-refine-948e9b6f` | `kai/axis-web:evolution-0f6149cb`（`9cd7b15f0310`） |
| postgres / redis / minio | 未动 | 未动 |

相对上次部署（`948e9b6f`）带入的改动：`7b97af92` 合并 develop，以及

- **后端**（`41772bc0`/`951d0220`）：`sandbox/e2b.py` 给工作区收集单独的超时
  （`_COLLECT_REQUEST_TIMEOUT_SECONDS = 900`，此前继承连接 `request_timeout`，CubeSandbox
  固定 30s，模型已答完的 Run 会在收集阶段被判失败）、`_handle_unexpected_error` 补 traceback、
  `studio/authoring_stream.py` 记录被拒绝的 builder 轮次、`worker/orchestrator.py` 一行；
- **前端**（`c51b9022`/`0d657cc5`/`7c76d761`/`0f6149cb`）：侧边栏二级菜单（新增
  `nested-menu.tsx`、`lib/sidebar-state.ts` 的展开态与项目列表缓存）、任务标题悬停滚动、
  新任务导航、项目下载控件改为纯图标。

无新增数据库迁移：`migrations/` 与上次部署逐字节相同，DB `alembic_version` 与镜像
`alembic heads` 均为 `0036`，因此没有执行迁移。

## 2. 构建方式

沿用该栈既有的**增量叠加**做法（`/data/agent-studio-evolution-20260920/`）：

- api：`FROM kai/axis-api:evolution-webtools-948e9b6f`，只替换
  `/app/project/lib/python3.12/site-packages/harness` 与 `/app/migrations`；
- web：`FROM kai/axis-web:evolution-refine-948e9b6f`，`rm -rf /app/.next` 后覆盖
  `server.js` / `.next` / `public` / `package.json`，**保留基座 Linux `node_modules`**
  （standalone 的 `node_modules` 不入包）。

Web 产物在 macOS 上用仓库锁文件构建（Node 22.22.2、Next 16.3.3，与基座一致），
`web-runtime` = `.next/standalone` + 应用 `.next/static` + `public`。

两个刻意的取舍：

1. **不覆盖 `/app/agents`**：镜像里比仓库多 4 个文件
   （`lead-agent/skills/skill-authoring-quality/*`、`skill-creator/SKILL.md`，来自基座镜像血统），
   整目录覆盖会把它们删掉；本次分支对 `agents/` 也无改动。
2. `/app/platform-skills` 已核对与分支**逐文件相同**（聚合哈希一致），`/app/scripts` 只有
   `seed_docker.py`（非分支内容），均无需处理。

## 3. 验证证据

内容对账（同一条命令形状，`find . -type f | LC_ALL=C sort | xargs sha256sum | sha256sum`）：

| 位置 | `harness` 聚合 sha256 |
| --- | --- |
| 本地 `0f6149cb` 干净 worktree | `0124a575cfe8293768c6913d1382a29fe4c5bbc6237f3e527342714d1a30ef0a` |
| 173 上传的发布目录 | 同上（另逐文件 diff：324/324 全等） |
| 容器内 `/app/.../harness` | 同上 |

> 注意：`sort` 受 locale 影响。Linux 默认 UTF-8 locale 与 macOS 默认排序不同，聚合哈希会
> 因为**行序**不同而不同（内容完全相同时也会），对账必须两边都加 `LC_ALL=C`。
> `harness` 共 324 个文件，与 `git ls-tree -r 0f6149cb src/harness` 数量一致，工作树无未提交改动。

运行时：

- `api`/`worker`/`web` 三个容器 **healthy**；`/healthz` → `{"status":"ok"}`；3302 `/`、`/login` → 200。
- Web 产物标记：从 3302 实际抓取的 `/_next/static/chunks/084_4wv8tc3gl.js` 含
  `harness:sidebar:`、`sidebar-projects:`、`nested-menu`；**旧镜像内为 0 命中**，
  证明新前端确实生效。镜像内 `BUILD_ID` = 本地构建 `WtkAibqqeCGgbKau0XmCk`。
- api 镜像内 `harness.runtime.web_tools` md5 = `dbac696d…`（与上次部署一致，未回退）；
  `import harness.sandbox.e2b / studio.authoring_stream / worker.orchestrator` 通过；
  `alembic heads` = `0036`。
- **真任务**：验证账号登录 8802 → 建会话 `session_85eb00b3…` → `POST /v1/sessions/{id}/runs`
  → `run_5adefc1e…` 事件序列
  `run.queued → sandbox.provisioned → run.running → model.route.selected → message.delta
   → message.completed → workspace.archived → run.succeeded`，终态 **succeeded**。
  沙箱 provider 为 `cubesandbox`（打到 172.20.109.111:13000），即本次部署的收集超时改动
  正好在被使用的路径上。

## 4. 回滚

```bash
ssh 173
cd /data/agent-studio-evolution-20260920
# 备份：backups/0f6149cb/（compose.json 与两份 *_SOURCE_REVISION，mode 700）
cp -p backups/0f6149cb/compose.json compose.json
docker compose -f compose.json up -d --no-deps api worker web
# 上一版本：api/worker = kai/axis-api:evolution-webtools-948e9b6f，web = kai/axis-web:evolution-refine-948e9b6f
```

镜像与发布目录都保留：`kai/axis-{api,web}:evolution-0f6149cb`、
`/data/agent-studio-evolution-20260920/release-0f6149cb/`。

## 5. 过程记录

- 本次同时有另一个 WorkBuddy 会话在同一 worktree（`~/.codex/worktrees/agent-evolution`）
  构建同一 revision，两边争抢 `.next` 导致构建互踩；按用户要求停掉该会话后，改为在
  独立 worktree（`/tmp/evo-0f6149cb`，detached 于 `0f6149cb`）构建。
  **同一分支要部署时，先确认没有别的会话在同一个 worktree 里构建。**
- 173 时钟比本机慢约 112s（解包时 tar 报 `time stamp … is in the future`）。对本栈无影响，
  但跨主机 worker 的租约/取消窗口对时钟敏感，多主机场景前需要 NTP 对齐。
