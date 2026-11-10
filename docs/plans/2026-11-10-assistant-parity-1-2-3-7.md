# 助理能力对齐：1 定时任务记录 / 2 聊天历史与文件 / 3 模型 Auto / 7 自动展开云电脑

> 2026-11-10 · 分支 `auto/bot-runtime-alignment-20261109`
> 原型：`小树2.0_完整版.html`（历史 aside、执行记录内页、模型菜单）

## 进度

- 已实现并上 116：提交 `c411c378`（3、7）+ `34f8889f`（1、2），镜像
  `kai/alignment-web:assistant-recall-20261110`。
- 回滚：`skill-loop/compose.before-assistant-recall.json`。
- 验证：`/`、`/assistant` 200；容器 healthy；分块含
  `assistant-history-located` 与「聊天记录」；新增测试 10 项（`tests/duty-run-history.spec.tsx`
  4 项、`tests/assistant-history-panel.spec.tsx` 6 项），全量仍只有
  `tests/assistant-viewed-marks.spec.tsx` 的 2 项既有失败。

## 1 定时任务记录与跳转

- `DutyEditDialog` 底部「运行记录」→ `DutyRunHistory` 内页（同一任务列表位置替换，
  返回后回到任务列表）。
- 数据：`GET /objectives/{id}/executions`（最多 100 条）。`Execution` 补 `triggerKey`，
  按命名空间显示 定时/手动/委派/管理：`schedule:`、`manual:`、`delegate:`、`manage:`。
- 状态筛选：已完成 / 执行失败 / 执行中 / 已取消；客户端 10 条一页，显示
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
- 定位：`locateInChat(messageId)` 查 `[data-message-id]`，`scrollIntoView` 后加
  `assistant-history-located` 1.8 秒（样式在 `assistant-conversation.css`），与原型
  `paLocateInChat` 一致；找不到时提示「未在当前会话中找到这条消息」。
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

## 待办

- P1b 新建任务仍缺 `POST /objectives` 的 `specialistId` 透传。
- 能力 4/5/6（主动提炼 Skill、群聊 @ 后代办、项目资产打通）需要后端事件或上下文注入。
- 79 Web 部署仍缺可用 SSH 凭据。
