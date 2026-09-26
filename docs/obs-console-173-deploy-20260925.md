# 轨迹观测控制台上 173 演化栈（2026-09-25）

在 3302 控制台新增会话级"调用轨迹"观测页：header「更多」菜单底部入口（`调用轨迹`）、时长/轮次/调用摘要、输入/模型/工具三泳道时间线（带轮次刻度与分隔线）、系统/上下文/用户/助手/工具/子任务/产物/审批事件流、右侧详情抽屉（概述/系统提示词/工具/参数/结果/计时）。分支 `auto/obs-console-173`（基于 `auto/agent-evolution` 的 `6d28480c`）。

## 1. 提交与功能

> r10b + api overlay（`d96165bf`）：**对齐 DSH 概览丰富度的第一批后端改动**。核对确认工具结果全文本就在 RunEvent 里，1200 字符只是投影层裁剪——`_tool_result_preview` 上限提至 8000（`_RESULT_PREVIEW_LIMIT`，仍走 redact_text 脱敏），api/worker overlay 镜像 `kai/axis-api:evolution-obsconsole-d96165bf`（血统对账：镜像 harness 与分支逐文件一致、仅差 activity.py 8 行；部署前确认无在跑 Run；进程内断言 preview limit=8000）。web r10b（BUILD_ID `5qcSEy0KFn-7F8Jr-_Idj6`）：工具节点概述渲染知识引用列表（citations），工具条新增 tokens 汇总 chip（runtime.result 聚合，取各 Run 最大值）。端到端：真实 Bash 任务结果预览 8000 字符（旧上限 1200）、tokens chip 19.3k。注意：web 打包前必须重新 `next build`（r10 首包就因复用旧 .next 被 BUILD_ID 指纹识破）。per-call token 投影需 runtime mapper 改动，本轮未做。

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

> r11（`3jYFRyAgWikcVB3fHSYp8`，web `evolution-obs-console-r11`，api/worker 保持 `evolution-obsconsole-d96165bf`）：修复用户指出的顶部时间条与统计问题。`runtime.system` 的“模型正在处理/运行时状态更新”不再生成步骤，避免重复“处理过程”；顶部时长/时间轴总窗口与每轮窗口优先按 `run.queued/running → run.succeeded/failed/cancelled/timed_out` 的真实生命周期计算，缺生命周期事件时按当前 Run 自己的节点回退（不再把第一轮的节点时间当第二轮起点）。全套 944 测试通过，现存/筛选/上下文探针全过。

> r12/r13（r13 BUILD_ID `TNDWcE8UZ8-TiQS7siTzY`）：修复时间条视觉问题。r12 先将瞬时系统/上下文/用户事件从持续阶段条过滤，避免最小宽度渲染为连续点；时长/轮次按 Run lifecycle。r13 在每轮生命周期窗口下增加连续淡色背景带，模型/工具等真实持续阶段作为叠加块，瞬时事件仍只在下方事件流显示。截图 `runs/trace-r13-timeline.png`，回归探针通过。

> r14（web BUILD_ID `OLFwBWlX-BEh0hRRPZX6b`）：时间条阶段块支持 hover/focus 观测 tooltip，显示徽标/名称、开始时刻、持续时长、状态和摘要；tooltip 探针实测助手阶段 `1.2s · succeeded` 并带摘要。r13 的连续轮次背景带保留，瞬时事件仍只在事件流展示。

> r16/r17（r17 BUILD_ID `GFUtgtf_x_4etoxNOVhZ2`）：按 DSH 可见能力补“阶段进度”总览：排队、环境、权限/资源、模型路由、运行时、思考/回复、工具/审批、产物、终态。每阶段只取已有 RunEvent/activity 事件；有数据显示完成/失败，未采集明确标灰“未采集”，不造值。r16/r17 同时保留可缩放时间画布（1×/2×/4×）、每轮连续背景带、模型/工具阶段叠加、瞬时事件下沉事件流。

> r18（BUILD_ID `8hVSrINPZFqye6H7wQm9y`）：按用户裁决重做时间轴。①移除阶段 pill 行（排队/环境/…/终态）及其数据层；②移除轮次刻度、轮次分隔线与轮次背景带——时间轴不再区分轮次，模型/工具等持续阶段按真实时间连续铺满整条时间轴（对齐 DSH 截图样式）；③缩放除 ± 按钮外支持触控板双指捏合（ctrl+wheel 非被动监听，1×/2×/4× 阶梯），画布横向滚动。hover tooltip、事件流完整瞬时事件、类型筛选均保留。截图 `runs/trace-r18-timeline.png`，tooltip/console 探针通过。

> r21/r22（r22 BUILD_ID `4VXvWYI7odeaWwV9-_naE`）：**时间轴改为“处理时长打包轴”**——轮次之间的墙钟空闲被压缩，每轮宽度 ∝ 该轮处理时长（10s 会话铺满；多天会话不再坍缩成两个点，修掉大量黑色空白）。块在轮内按比例定位；hover tooltip 仍显示绝对时钟。同时恢复双指捏合缩放（ctrl+wheel，以光标为锚点）与横向滚动，去掉 ± 按钮；输入泳道只画用户消息刻度，上下文瞬时行只留在事件流。

> r24（BUILD_ID `68WHC96_UagvOQPU43xfw`）：①时间条泳道配色与事件流徽章对齐——输入琥珀、模型绿、工具橙棕、失败红（此前全部同色绿，泳道不可辨）；②hover tooltip 改为 portal 到 body、跟随光标的浮动定位，不再被横向滚动容器裁剪（用户报告"移动上去被遮挡"）；探针同步改为 body 作用域并验证 hover/focus。

> r25（BUILD_ID `pUHtQ22Ftz4IhNWIwngMN`）：修复用户输入泳道为空——渲染时误用阶段块 map 取刻度位置（取不到即返回 null）。现按泳道选择 anchors：输入行用 stripTicks（固定 4px 琥珀刻度），其余泳道用 stripBlocks。注意：同一 tag 原地重建镜像后，部署脚本 base 断言会失败（在跑 tag==目标 tag），需手动 build+compose up。

> r26（BUILD_ID `7V9Pdu0oxh3A33Ju7RqgA`）：hover 气泡改为紧凑深色气泡（11px 字号、深底圆角、开始→结束时刻+耗时+摘要预览），并移除色块上的原生 title 提示（用户反馈"字体太大"的大字提示实为浏览器原生 tooltip 与气泡重复）。部署插曲：同一 r26 脚本链上 base 与 compose 断言需与实际在跑 tag 同步（r25→r26 两次 sed），否则 build 成功后卡在断言。

> r27（BUILD_ID `onQqT--mB-ivaUjh0771W`）：修思考过程碎片化。根因：思考流事件带 item_id（非 message_id），客户端按 message_id 分组退化为每 delta 一行（"Op"、"is" 之类的单词行）。修复：按 item_id 分组 + 相邻思考片段（间隔 <600ms、中间无工具/审批）合并为一行。时间轴：思考是真实模型处理时间，strip 布局把同泳道 ≤2s 间隙的阶段合并为连续块（思考+回答构成连续模型活动，与 DSH 一致）；事件流保留独立思考行。

> r28（BUILD_ID `aQk3qTc0i01ttjXt6Yn1Y`）：详情"概述"整合化——按 DSH 形态把 参数/结果/来源引用/计时 作为概述内可折叠分区（参数、结果默认展开，带 › 旋转指示），状态行常驻；独立页签保留便于快速跳转。Schema 分区未做：活动事件不含工具 schema（在运行时注册表，需后端开放），不造假数据。

> r29（BUILD_ID `b96uTDW1HmEiNGgCMjJUU`）：失败/错误在时间轴上标红。两处修复：①CSS 优先级——泳道配色规则（r24 引入）与 `.is-failed` 同优先级且靠后，红色被覆盖（用户此前看到失败块仍显示泳道色）；现在状态色显式覆盖泳道色。②"异常"类瞬时事件此前根本不上时间轴（零时长被过滤），现在画为工具泳道上的红色 5px 刻度。

> r30（BUILD_ID `p7uohFxSSK5d0ZKFukN5P`）：①顶部移除总时长 chip（对齐 DSH/ZCode：时长按环节看，不做会话总时长统计）；②详情抽屉左上角徽章使用泳道配色（与时间条色块一致）；③概述中"计时"分区默认展开；④脚注的"外部 Trace"改为可直接点击的链接（运行中 Run 的 Langfuse 地址），无运行时提示入口在对话过程卡片的「运行详情」。

> r31（BUILD_ID `L1pTUsAlBAaCj3xmbs76d`）：概述各分区（参数/结果/来源/计时）之间去掉横线分隔，仅保留间距。

> r32（BUILD_ID `CN-KtHAvfKeAGuSYdNrHV`）：⑤思考收进"助手"——列表不再单独出思考行；助手节点吸收其前的思考片段（时间轴块覆盖思考+回答），详情抽屉新增"思考"页签与概述折叠分区（无后续回答的思考保留独立行）；⑥资源/权限/工具连接等上下文事件合并为每轮一条"运行上下文"（详情含全部事实与技能清单），时间轴上以绿色刻度标识（与用户输入的琥珀刻度区分，对齐 DSH 绿色）。

> r33（BUILD_ID `XOxCN1BUFy9kapkuyY6xE`）：每轮耗时可见——事件流"第 N 轮"分隔行标注该轮处理耗时（与打包轴同口径：轮内活跃时段，不含轮间空闲）；顶部"轮次"chip 悬停显示每轮耗时明细。

> r34（BUILD_ID `aRXZ-1woS_4t2uDU7lNVI`）：用户/上下文区分展示——事件流徽章与时间轴刻度：用户=琥珀、上下文=绿色（`.is-context`，与 DSH 绿对齐）；根因是 `.block.is-ok` 通用绿底覆盖了琥珀泳道色，已移除（泳道类接管配色）。

> r35（BUILD_ID `WH1gHd-j9EQuD27ejo516`）：上下文独立泳道——时间轴四条：输入（琥珀用户刻度）、上下文（绿色刻度）、模型（绿块）、工具（橙块）。此前用户与上下文刻度同在输入泳道且起点几乎重合（都在 0%），绿色盖住琥珀。事件流徽章同步：上下文绿、用户琥珀。
