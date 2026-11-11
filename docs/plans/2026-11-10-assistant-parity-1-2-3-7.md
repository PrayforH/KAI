# 助理能力对齐：1 定时任务记录 / 2 聊天历史与文件 / 3 模型 Auto / 7 自动展开云电脑

> 2026-11-10 · 分支 `auto/bot-runtime-alignment-20261109`
> 原型：`小树2.0_完整版.html`（历史 aside、执行记录内页、模型菜单）

## 进度

- 已实现并上 116：提交 `c411c378`（3、7）+ `34f8889f`（1、2），镜像
  `kai/alignment-web:assistant-recall-20261110`。
- 迭代：提交 `61c0c491`（定位翻页、触发类型补全、抽屉位置），镜像
  `kai/alignment-web:assistant-recall-20261110-v2`。
- 回滚：最新一步用 `skill-loop/compose.before-assistant-recall-v2.json`；
  退回第一版用 `skill-loop/compose.before-assistant-recall.json`。
- 验证：`/`、`/assistant` 200；容器 healthy；分块含
  `assistant-history-located` 与「聊天记录」；本轮测试 16 项（执行记录 5、
  聊天记录 6、定位翻页 3、自动展开 2），全量 282 个文件 1731 项通过 /
  1 项跳过（顺带修正了 `tests/assistant-viewed-marks.spec.tsx` 里按旧结构
  断言电脑入口的 2 项）。

## 1 定时任务记录与跳转

- `DutyEditDialog` 底部「运行记录」→ `DutyRunHistory` 内页（同一任务列表位置替换，
  返回后回到任务列表）。
- 数据：`GET /objectives/{id}/executions`（最多 100 条）。`Execution` 补 `triggerKey`，
  按后端真实写入的命名空间显示 定时/手动/飞书/委派/管理/演练：`schedule:`、`manual:`、
  `feishu:`、`delegate:`、`delegate-specialist:`（交接，归入委派）、`manage:`、`trial:`。
  命中 100 条上限时补充说明「更早的记录暂不可见」。
- 状态筛选：已完成 / 执行失败 / 执行中 / 排队中 / 已取消；客户端 10 条一页，显示
  `共 N 条 · 第 x/y 页`。
- 跳转：`taskThreadHref(threadId, runId)` → `/?thread=…&run=…`；没有 `runId` 的执行
  标注「尚未产生会话」，不给死链。
- 解析逻辑（`triggerKind` / `filterExecutions`）是纯函数，便于单测与后端分页落地后复用。

## 2 聊天历史与文件定位

- 顶栏新增「聊天记录」按钮（仅助理页），打开右侧抽屉 `AssistantHistoryPanel`。
- tabs：全部 / 文件 / 图片与视频；搜索：聊天记录或文件名称，图片 tab 按原型提示
  「暂不支持搜索图中文字」。
- 筛选：发送人（我 / 助理）；文件 tab 另有类型（图片/视频/PDF/HTML/压缩包/其他），
  文件与图片 tab 有日期。**历史接口没有逐条时间戳**，日期只取产物索引的
  `created_at`，消息行不自造时间。
- 文件与消息关联：产物带 `run_id`，消息 id 约定 `assistant-<runId>[-suffix]`
  （`messageOwnsArtifact`，与 `agent-thread.ts` 的 `messageOwnsRun` 同口径）。
  用户上传的附件来自消息 content 的 input part，直接挂在自己的消息上。
- 定位：`src/lib/chat-locate.ts` 先查已挂载的 `[data-message-id]`；assistant-ui 只挂载
  已导入的几页消息，找不到时通过 `registerHistoryLocator`（`agent-thread.tsx` 注册）
  让会话 `loadEarlier()` 往回翻页再试（最多 5 页），找到后 `scrollIntoView` 并加
  `assistant-history-located` 1.8 秒（样式在 `assistant-conversation.css`），与原型
  `paLocateInChat` 一致；翻完仍找不到提示「未在当前会话中找到这条消息」，翻页失败
  提示「未能加载更早的消息，请重试」。
- 抽屉：右侧浮层从顶栏下方展开（`top: var(--shell-header-height)`，顶栏保持在上层），
  离开助理面自动收起；≤640px 时占满宽度。
- 文件预览：产物派发既有 `harness:preview-artifact` 事件，助理页复用居中预览弹窗；
  上传附件走 `/api/input-artifacts/{id}/content?inline=1`。
- 已知取舍：每次打开读全量历史（最多 20 页 × 100 run）+ 200 个产物；对超大历史后续
  可改为按需分页，接口本身支持 `before` 游标。

## 3 模型 Auto

- 下拉默认项：`自动 · <Agent 默认模型>`；`title` 说明自动规则：用 Agent 默认模型、
  读图需要切到支持视觉的路由、不可用路由不出现。
- 状态文字：自动 / 仅本次任务 / 当前对话 / 视频生成。
- 可解释记录不另存：每次运行的模型选择已在轨迹页「模型」泳道。

## 7 自动展开云电脑

- `desktop-occupancy-chip.tsx` 导出 `useDesktopOccupancy` 与 `occupancyHolding`，
  轮询代码仍只有一份；chip 与页面共用。
- 助理页在真实 `holder.execution_id` 出现且电脑未展开时自动打开一次；
  `dismissedExecution` 记住手动关闭的执行，同一执行不再弹回；新执行可再次联动。

## 长期记忆写入链路修复（2026-11-11）

- 现象：助理调用 `propose_memory` 后只回一句「待确认 / 我搜不到」，用户以为写入失败。
- 根因 1（交互）：`propose_agent` 默认 `pending`，需要设置页确认；对话里没有任何入口，
  用户在聊天里看不到「去哪确认」。
  → 新增 `memory-proposal-card.tsx`：工具结果直接渲染卡片，pending 给「确认保存」
  （调 `memory-bank/entries/{id}/confirm`，带 `expectedVersion`），active 显示
  「已写入长期记忆」，`untrusted-memory-deny` / `InputValidationError` 分别说明原因。
  历史消息只有运行视图结果摘要时也按同一套解析（5 个用例覆盖）。
- 根因 2（自动提取）：116 的 `HARNESS_MEMORY_EXTRACTION_BASE_URL` 缺 `/v1`，
  provider 根路径返回 HTML SPA，70 个任务全部 `memory_extraction_JSONDecodeError`。
  → `compose.private.json` 里 `services.api` / `services.worker` 改为
  `http://172.20.109.174:4000/v1`（递归 diff 只允许这两处变化，
  备份 `skill-loop/compose.before-memory-extraction.json`），重建 api/worker。
  重跑验证：任务从 `failed` 转为 `completed`，新产生 pending 条目
  「用户希望以后建议都输出为 HTML 精美格式。」；直接用容器内 `MemoryExtractor`
  probe 也返回同一条候选，证据逐字命中。
- 残留：少量任务 provider 返回 `content: null` / 非 JSON（`AttributeError` /
  `ValidationError`）。已在 `extraction.py` 加固：空补全按「无候选」直接完成，
  包裹在说明文字里的 JSON 截取完整 `{...}` 再校验（`0dd78195`、`52231ece`，
  14 项单测通过）。后端镜像 `kai/alignment-api:memory-fix-20261111-v2` /
  `kai/alignment-worker:memory-fix-20261111-v2` 已重建并重建容器
  （备份 `skill-loop/compose.before-memory-fix-v2.json`）；原先失败的两个任务
  重排后 1 次即 completed，当前无 retrying。
  旧的 58 条 JSONDecodeError 任务未自动回填，需要时按
  `status='pending', attempts=0` 重排即可。
- 验证：283 个测试文件 / 1736 通过 / 1 跳过；tsc 无错误；116 web 升到
  `kai/alignment-web:assistant-recall-20261110-v4`（备份
  `skill-loop/compose.before-memory-card.json`），`/`、`/assistant`、`/settings`、
  `/studio/automation` 均 200，新 chunk 含「记忆待确认 / 确认保存」标记。
- 提交：`55aa5d86`。

## 长期记忆拦截粒度：按出处放行（2026-11-11）

- 现象：会话里由用户直接说出的偏好（「以后报告都用 HTML」）也被
  `untrusted-memory-deny` 拦掉；而同一轮的自动提取却能写进去，看起来自相矛盾。
- 根因：`context_trust` 是**会话级单调水位**（`session_context_state.trust_high_watermark`），
  同会话里只要发生过一次 `WebSearch` / `WebFetch`，后续所有工具都按 UNTRUSTED 处理；
  策略层拿不到「这句话是谁说的」，只能整段拒绝。
- 改法（出处校验）：
  - `propose_memory` 新增必填 `evidence_quote`（4–500 字；单字引文不判定为出处），描述里写明必须是用户原话、
    不得取自网页或工具输出；`memory_tools` 校验后随提案写入 `evidence`。
  - `SdkToolGate.pre_tool_use` 在判定前做逐字比对：`evidence_quote` 是
    `run.input["prompt"]`（用户本轮输入）的子串时才把**策略视角**的
    `ContextTrust` 降回 SAFE；审计里的 `context_trust` 仍记录真实值，
    并另记 `provenance_verified: true`。
  - 网页/工具结果里的句子永远不是用户 prompt 的子串，所以「引网页冒充用户」仍被拒绝；
    `audit_redaction` 把 `evidence_quote` 与 `content` 一起打码。
  - 卡片文案改为说明：本会话读过外部内容后需附用户原话作出处，或新开一个会话。
- 测试：`test_sdk_tool_gate.py` 新增
  `test_memory_proposal_passes_untrusted_session_only_with_user_quote`
  （网页原句 DENY / 用户原句 ALLOW，审计保留 `context_trust=untrusted` 与
  `provenance_verified=true`，用户原话不出现在事件里）；`test_memory_mcp.py`、
  `test_memory_tools.py` 同步补 `evidence_quote`；卡片用例 5 项通过。
- 两个入口同步：HTTP MCP（`memory_bank/workload.py`，跑在 api 容器）与
  in-process SDK 工具（`runtime/memory_tools.py`，跑在 worker）都要求并持久化
  `evidence_quote`，`test_memory_mcp.py` 断言 `source.evidence` 落库。
- 部署：79 与 116 各自用「现网 api/worker 镜像 + 8 个补丁文件」增量构建，
  标签 `kai/alignment-api|worker:memory-provenance-20261111-79|-116`；
  compose 备份 `compose.before-memory-provenance.json`，递归 diff 仅 api/worker
  的 image 字段。容器内探针确认：默认路由 `deepseek-flash`、schema 必填
  `["content","evidence_quote"]`、用户原句命中 `True` / 网页句子 `False`；
  79 `http://127.0.0.1:8000/health` 200、116 边缘 3543 200 与 api `/healthz` 200。
- 未发布：web 卡片文案改动只在代码里，79（`79-82-83-20261111-v6`）与 116
  （`desktop-pane5-20261111-116`）现网 web 仍是旧文案，等下次 web 发布一并带。

## 顺带修复：新草稿默认路由仍指向已下线路由（2026-11-11）

- 现象：`d950f5d9` 下线 V4/pro 路由后，`factory.create_draft_spec` 仍默认
  `deepseek-v4-pro`，新建草稿一发布就报「模型路由未注册」；环境策略默认
  `allowedModelRoutes` 也停在旧五条（含 `new-api-default` / `glm-5-3-flash`）。
- 改法：默认路由改 `deepseek-flash`；`bundle_import` 缺省映射改
  `deepseek-flash` / `minimax-m3-1-flash`；`EnvironmentResourcePolicy` 默认
  允许清单收敛为现行两条；`tests/unit/assistant` 补 `__init__.py` 修同名测试
  收集冲突（`test_memory_tools` / `test_service` / `test_lifecycle` 撞名）。
- 安全说明：微信媒体引入 `padding.PKCS7` 后 cryptography VEX 回归测试开始失败；
  VEX 文案改为说明 `padding.PKCS7` 是分组填充、不在 CVE-2026-69247 的
  PKCS#7 消息解密路径，测试改为盯 `pkcs7_decrypt` / `pkcs7_load` / `smime`。
- 单测：`tests/unit` 2521 通过 / 9 失败，失败均为环境缺口（deepagents 可选依赖、
  langchain_core、本地 Postgres 未启动），与代码无关。

## 79 同步部署与「接管」问题（2026-11-11）

- 镜像按 79 现网基线增量构建，不跨环境复用：
  `kai/alignment-web:assistant-recall-20261110-v4-79`（FROM 现网 web 镜像 +
  v4 app）、`kai/alignment-api/worker:memory-fix-20261111-v2-79`
  （FROM 79 现网 api/worker + 新版 `extraction.py`）。
- 备份 `compose.before-recall-v4-79.json`；递归 diff 仅 5 处：web/api/worker 的
  image，以及 api/worker 的 `HARNESS_ASSISTANT_COMPUTER_TARGETS`。
- 验证：三个容器 healthy；`/`、`/assistant`、`/settings`、`/studio/automation`
  均 200；线上 chunk 含「记忆待确认 / 确认保存」。79 的提取环境本来已带 `/v1`、
  自动保存为 true；重排 5 个失败任务后 3 个 completed，剩余 1 个 ValidationError
  继续退避，历史 45 条 ValidationError / 8 条 AttributeError 未全量回填。
- 「能看不能接管」根因：`HARNESS_ASSISTANT_COMPUTER_TARGETS` 里 cloud 目标的
  image 仍是 `c50f2f…`，而注册表当前 `HARNESS_ASSISTANT_COMPUTER_VM_IMAGE`
  是 `498e0e…`。`ComputerTargetService.view` 用
  `(provider, image, volume_ref)` 全等匹配 `active_target`，image 不一致导致
  `active_target=null`；前端拿不到 `binding_key`，轮询抛
  「电脑已切换，正在重新确认控制状态」，`controlError` 置位后接管按钮禁用。
  两处 targets 的 cloud image 已改成 `498e0e…`，与注册表一致；当前没有活跃
  execution lease，接管应恢复，用户需硬刷新加载新 chunk。

### 远程电脑「接管不了 / 重连循环」（2026-11-11 追加）

- 现象：查看 Remote Computer（`local-32-45`）时鼠标点击无反应；点「重新连接」后
  连续重连停不下来，状态仍显示接管。
- 根因 1（查看的不是执行电脑）：接管只对「助理当前使用的电脑」有效。查看另一台时
  `viewTarget !== active_target`；viewer 按当前目标的 binding 读取控制状态，得到
  `observe` 并丢弃全部输入。前端底部却只写「<执行电脑>由你控制」，看起来像当前
  画面已被接管。
  → 占用提示改为「…由你控制 · 当前画面仅观看」，并加「切回<执行电脑>」；
  查看非执行电脑且无人接管时明确显示「当前画面仅观看：点『供助理使用』…再取得
  控制权」。
- 根因 2（租约绑在已消失的 viewer）：`viewer_id` 每次挂载随机生成，刷新页面就变成
  新的 viewer，旧租约仍记着旧 id 并保持 `human`（TTL 300s）。前端读到的是
  「另一个窗口已接管」，接管按钮禁用；每次连接成功又把重试计数清零，短命连接会
  无限重连。
  → viewer id 存 `sessionStorage`（同标签页刷新继续沿用）；连接稳定 10s 后才换取
  新的重试预算、才允许重跑 guest start；`HUMAN_TTL_SECONDS` 300→90（viewer 每秒
  续期一次，窗口只需覆盖一次重连）。
- viewer 增加排障日志：客户端关闭码、授权宽限过期、watch 退出的具体原因、控制状态
  变化，以及超过宽限的慢轮询。
- 验证：283 个前端测试文件 / 1738 通过 / 1 跳过（新增 2 项：瞬时断线不会无限重连、
  刷新后沿用 viewer id）；tsc 无错误；viewer 20 项单测通过；ruff 通过。
  79 已更新：web `kai/alignment-web:desktop-pane5-20261111`、viewer
  `kai/computer-viewer:session-log-20261111`（备份 `compose.before-desktop-pane5.json`），
  容器 healthy，`/`、`/assistant`、`/settings`、`/studio/automation`、
  `/assistant/computer` 均 200，线上 chunk 含「当前画面仅观看」与 `kai.desktop.viewer`。
- 116 同步：web 升到 `kai/alignment-web:desktop-pane5-20261111-116`
  （备份 `skill-loop/compose.before-desktop-pane5.json`，递归 diff 仅 web image），
  api/worker 镜像不变；五条路由 200，BUILD_ID 与本地构建一致，chunk 含
  「当前画面仅观看」。116 的 viewer 仍跑旧 api 镜像，接管租约 TTL 仍是 300s；
  若要和 79 一致（90s + 排障日志），下次连同 api/viewer 一起重建。

## 待办

- P1b 新建任务仍缺 `POST /objectives` 的 `specialistId` 透传。
- 能力 4/5/6（主动提炼 Skill、群聊 @ 后代办、项目资产打通）需要后端事件或上下文注入。
- 116 的 api/viewer 仍未带 90s 接管租约与 viewer 排障日志（当前只有 web 修复）。
