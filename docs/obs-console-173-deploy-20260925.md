# 轨迹观测控制台上 173 演化栈（2026-09-25）

在 3302 控制台新增会话级"调用轨迹"观测页：header「更多」菜单底部入口（`调用轨迹`）、时长/轮次/调用摘要、输入/模型/工具三泳道时间线（带轮次刻度与分隔线）、系统/上下文/用户/助手/工具/子任务/产物/审批事件流、右侧详情抽屉（概述/系统提示词/工具/参数/结果/计时）。分支 `auto/obs-console-173`（基于 `auto/agent-evolution` 的 `6d28480c`）。

## 1. 提交与功能

> r8/r9 追加（`d96165bf`，线上 `evolution-obs-console-r9`，BUILD_ID `2mhJe9q9oAuQdlMMiOmSD`，基座 r8）：**上下文行内容充实**——后端事件投影只带计数（skill_count/工具数量/policy_id），Agent 资源行复用草稿清单出「技能」页签；全部上下文行详情内嵌「会话上下文」面板（`loadThreadContext` 窗口快照 + 最新 digest 事实/决定/待办，实测 4937/200000 tokens 及真实 digest 文本）。修复：面板加载守卫改 ref（state 标志因 deps 变化使在途 fetch 的 cleanup 失效，面板永远停在加载中）。部署血统注意：重部署脚本的 base 必须等于当前在跑 tag（r8 首次部署后即不满足 base=r7，静默 exit 1）。

> r7 追加（`ba6e9bff`）：思考过程行（reasoning.delta 独立成 span，紫色徽标，不与助手消息混流）、事件类型筛选弹层（系统提示词/上下文/用户消息/思考过程/助手消息/工具调用/子任务产物审批 共 7 个开关，localStorage 持久化，联动列表与时间线）、详情抽屉上一步/下一步 + ↑/↓ 键盘导航（16 步实测）、用户消息详情"在对话中查看"跳转。线上 `kai/axis-web:evolution-obs-console-r7`（BUILD_ID `d1IBo9vRgpM7BsQ14U_sS`，基座 r6）。探针 `trace-filter-probe.mjs` 全过（筛选 17→15 行、恢复、导航、ArrowUp）。

| 提交 | 内容 |
| --- | --- |
| `7077d7c3` | `session-trace.ts` 数据层、轨迹控制台组件、菜单入口、933+ 项测试 |
| `4b11d387` | 子任务只取 started→completed/failed 里程碑并配对成 span，忽略 delta/progress/updated 帧 |
| `745847ba` | 部署记录 |
| `54a8af2c` | 按用户指令：去掉独立「轨迹」页签，入口收进 header「更多」菜单并置底；详情 ESC 关闭；审批生命周期折进工具节点（requested→approved/rejected 配对，等待审批单独标示）；事件流轮次分隔；运行结束自动刷新（busy→idle 触发） |
| `f0413777` | 修轮询守卫：以"已解析 Run 数"而非"历史消息数"为条件（运行中历史仅有 user 消息时旧逻辑停止轮询） |
| `a92ebcf5` | 系统/上下文节点 + 轮次刻度：每会话一条「系统」节点（能解析草稿时展示版本化系统提示词与技能清单，抽屉含 系统提示词/工具 页签；否则回退运行时事实），`policy.resolved`/`runtime.system`/`agent.assets.staged`/`workspace.restored/archived` 映射为「上下文」行，时间线按轮次画刻度标签与分隔线 |

数据来源：AG-UI history 为每轮 Run 内嵌完整 `harness_run_activity` 负载，纯客户端投影；系统提示词经 `studio drafts`（`listAccessibleDrafts`→`getDraft`，按 name+publishedVersion 匹配）解析并按版本缓存，无后端改动。权限沿用现状（用户已确认后续再收紧）。

## 2. 部署（r1→r6，api/worker 全程未动）

- 当前线上：`kai/axis-web:evolution-obs-console-r6`（BUILD_ID `Y0PufNXue0IgaR76K2KVs`，revision `a92ebcf5`），基座 r4。
- 发布目录 `/data/agent-studio-evolution-20260920/obs-console-r6/`，备份 `backups/obs-console-*/`；r5 未部署（被打断，内容已并入 r6）。
- 镜像血统：standalone + 自带 `node_modules`（44 顶层包，与 `skill-activity-a403d537` 一致）；旧冒烟脚本的 `require("rxjs")` 对该血统必然失败，已改为 BUILD_ID + `/`、`/login` HTTP 冒烟。

## 3. 验证证据

- 容器 healthy；chunk 指纹含 `session-view-tabs`→已移除页签后为 `turnDivider`/`待审批` 等新标记；`/login`、`/`、8802 `/healthz` 均 200。
- Playwright 探针（`demo/agent-builder/scripts/trace-console-probe.mjs`、`trace-live-probe.mjs`，只读或仅测试环境新任务）：
  - 现存任务：菜单入口打开、摘要 chips、三泳道、事件行、系统节点=1、上下文行=5-6、详情抽屉各页签、搜索过滤、返回对话按钮；
  - 草稿型智能体任务：系统节点为「初始系统提示词」，系统提示词页签渲染 625 字符真实内容，工具页签可用；
  - 实时链路：新任务发送消息 → 运行中轨迹页实时合并（1 轮次）→ 运行结束**无需手动刷新**自动载入持久历史（0→2 行、轮次分隔、无加载错误）。
- 全套单测 939 通过（`builder-conversation` 在基线提交同样偶发超时，为既有抖动，与本次无关）。
- 截图：`demo/agent-builder/runs/trace-final-multiturn.png`（轮次刻度+系统/上下文行）、`runs/trace-live-*/`（运行中与结束后）。

## 4. 回滚

```bash
ssh 173 && cd /data/agent-studio-evolution-20260920
cp backups/obs-console-7077d7c3/compose.json compose.json   # 回到 r1 前基线（skill-activity-a403d537）
docker compose -f compose.json up -d --no-deps --no-build --wait web
```

## 5. 已知边界与后续

- 系统提示词目前对所有可解析草稿的智能体可见（用户已确认，权限后续再收紧）；系统智能体（如 lead-agent 无草稿）回退为运行时事实版系统节点。
- DSH 的逐条模型消息上下文（system-reminder 级）需要模型 I/O 级数据，当前 RunEvent 不含；平台已有会话上下文读模型（context-client 的 digest/窗口），后续可在轨迹页加"上下文窗口"下钻。
- 审批在无匹配 tool.request 时单独成行；审批等待的持续段暂以点表示，未画持续条。
- 待人工验收：整体视觉与交互、暗色主题下的观感、长会话（20+ 轮）滚动与搜索性能。
