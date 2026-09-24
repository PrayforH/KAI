# 轨迹观测控制台上 173 演化栈（2026-09-25）

在 3302 控制台新增会话级"轨迹"观测页（对话/轨迹页签、时长/轮次/调用摘要、输入/模型/工具三泳道时间线、事件流列表、右侧详情抽屉），分支 `auto/obs-console-173`（基于 `auto/agent-evolution` 的 `6d28480c`）。

## 1. 部署内容

| 提交 | 内容 |
| --- | --- |
| `7077d7c3` | `session-trace.ts` 数据层（历史消息提取每轮 activity、节点成段、过滤）、`run-trace-console` 组件与样式、页签集成、任务菜单"调用轨迹"改为应用内切换、933 项测试 |
| `4b11d387`（r2） | 子任务只取 started→completed/failed 里程碑并配对成带时长 span，忽略 `subagent.delta/progress/updated` 高频帧 |

数据来源：`GET /api/agui/threads/{id}/history?limit=100` 分页取全量可见 Run，每轮 Run 的完整 activity 负载由服务端嵌入在 `harness_run_activity` 工具调用参数里（`harness-activity-{run_id}`），纯客户端投影，无后端改动；活跃 Run 用现有 activity store 实时合并。纯前端变更，api/worker 未动。

## 2. 构建与发布

- 本地（worktree `/tmp/obs-console-173`）`npm ci --ignore-scripts` → `vitest run` 932 通过 → `next build`。
- standalone 产物 + `.next/static` + `public` + `node_modules` 打包 overlay（与 `skill-activity-a403d537` 血统一致，镜像自带 standalone node_modules，44 顶层包；macOS 构建该血统已在现网验证可运行）。
- 173 `/data/agent-studio-evolution-20260920/`：
  - r1：`kai/axis-web:evolution-obs-console-7077d7c3`，BUILD_ID `3KPp2Ehg0i2rjbPzdQ7U0`；
  - r2（当前）：`kai/axis-web:evolution-obs-console-r2`，BUILD_ID `FNyA5WWnF4J1A8EcFAjel`，compose.json web 镜像原子替换，备份在 `backups/obs-console-*/`，api/worker 容器全程未动。
- 首次冒烟脚本沿用旧全量依赖检查（`require("rxjs")`）对 standalone 血统基座必然失败，已改为 BUILD_ID + `/`、`/login` HTTP 冒烟。

## 3. 验证证据

- `docker inspect`：web healthy（r2 镜像），api/worker healthy 且镜像前后一致（脚本 diff 断言）。
- chunk 指纹：容器内 `/app/.next/static/chunks/29hbgphtq2e31.js` 含 `session-view-tabs`；CSS 含 `.run-trace-console-module__*`。
- Playwright 探针 `demo/agent-builder/scripts/trace-console-probe.mjs`（复用 harness 登录态，只读不写任务）对 3302 全部通过：页签切换、摘要 chips、三泳道、事件行、详情抽屉（概述/参数/结果/计时）、搜索过滤；截图见 `runs/trace-console-*`。带工具任务（QA附件失败恢复）验证工具行 `Read {"file_path":…} → …`、1.6s 时长与工具泳道 span。

## 4. 回滚

```bash
ssh 173 && cd /data/agent-studio-evolution-20260920
cp backups/obs-console-7077d7c3/compose.json compose.json   # 回到 r1 前基线
docker compose -f compose.json up -d --no-deps --no-build --wait web
```

## 5. 已知边界与下一步

- 详情页签按数据可用性自适应：工具行有 参数/结果；输入/助手行无参数页签（activity 负载不含工具 schema，故未设 Schema 页签，不伪造数据）。
- 时间线为单泳道线性投影，尚未展示审批等待的持续段；失败/运行中已有状态着色。
- 下一步：多轮长会话分页加载体验、时间线 hover tooltip、与开发者抽屉/Langfuse trace 的互跳、把探针并入常规回归。
