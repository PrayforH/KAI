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
  `ValidationError`），会按 5 次退避后置 failed；解析入口的健壮性需要后端镜像重建，
  暂未纳入本次 web 发布。旧的 58 条 JSONDecodeError 任务未自动回填，需要时按
  `status='pending', attempts=0` 重排即可。
- 验证：283 个测试文件 / 1736 通过 / 1 跳过；tsc 无错误；116 web 升到
  `kai/alignment-web:assistant-recall-20261110-v4`（备份
  `skill-loop/compose.before-memory-card.json`），`/`、`/assistant`、`/settings`、
  `/studio/automation` 均 200，新 chunk 含「记忆待确认 / 确认保存」标记。
- 提交：`55aa5d86`。

## 待办

- P1b 新建任务仍缺 `POST /objectives` 的 `specialistId` 透传。
- 能力 4/5/6（主动提炼 Skill、群聊 @ 后代办、项目资产打通）需要后端事件或上下文注入。
- 79 Web 部署仍缺可用 SSH 凭据。
