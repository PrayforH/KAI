# 分页会话编辑发送兼容修复实施计划

关联交接文档：[最近用户问题编辑功能交接](../../handover/2026-07-22-edit-latest-user-message.md)

> **For agentic workers:** 本计划在当前会话内联执行；步骤使用复选框跟踪，不自动提交 Git。

**Goal:** 修复分页会话编辑发送调用已废弃 `thread/rollback` 的失败，并验证桌面与移动端均可完成编辑发送。

**Architecture:** 对齐当前官方 TUI 的 prompt edit 语义，使用 `thread/fork { beforeTurnId }` 从被编辑问题前创建新任务，再复用现有 `turn/start` 发送与路由跳转链路。编辑器继续共用同一响应式组件，不新增移动端专用实现。

**Tech Stack:** React 19、TypeScript、Next.js 16、Codex app-server JSON-RPC、Vitest、Playwright/CDP。

## Global Constraints

- 所有 app-server 验证使用 `CODEX_HOME=/volume2/SSD/codex/Temp/codex-dev-home`。
- 以当前官方 `codex-rs/tui` 的 `ForkSessionForPromptEdit` 与 app-server `thread/fork.beforeTurnId` 为语义基准。
- 不修改 `/home/rrssnas/code/codex`，不引入第三方依赖，不使用真实 `CODEX_HOME`。
- 只修改编辑发送必要链路；保留其它既有 rollback 能力和跨客户端兼容代码。
- fork 或发送失败时保留原编辑器内容，不跳转、不伪造成功状态。

---

### Task 1: 编辑 fork 协议与编排

**Files:**
- Modify: `src/codex-web/AppServerProvider.tsx`
- Create: `src/codex-web/edit-message-fork.ts`
- Test: `src/codex-web/tests/edit-message-fork.test.ts`

**Interfaces:**
- Consumes: `threadId`、被编辑消息的 `turnId`、现有 `forkThread`、发送与导航回调。
- Produces: `forkThread({ beforeTurnId })` 成功后才发送，发送成功后才导航的编排函数。

- [x] **Step 1: 编写失败测试**

覆盖 `beforeTurnId` 请求参数、`fork -> send -> navigate` 顺序，以及 fork/发送失败不执行后续动作。

- [x] **Step 2: 运行红灯测试**

```bash
CODEX_HOME=/volume2/SSD/codex/Temp/codex-dev-home npx vitest run src/codex-web/tests/edit-message-fork.test.ts
```

Expected: FAIL，因为编排模块和 Provider 参数尚不存在。

- [x] **Step 3: 实现最小协议动作与编排**

扩展现有 `ForkThreadParams` 支持 `beforeTurnId`，并实现仅负责顺序控制的纯异步函数。

- [x] **Step 4: 运行定向测试**

运行步骤 2 命令，Expected: PASS。

### Task 2: 页面与编辑器接线

**Files:**
- Modify: `src/app/chat/[id]/page.tsx`
- Modify: `src/components/chat/ChatView.tsx`
- Modify: `src/codex-web/tests/app-server-message-edit-wiring.test.ts`
- Modify: `src/codex-web/tests/latest-user-message-edit.test.ts`

**Interfaces:**
- Consumes: 最近可编辑用户消息的 `turn_id`、编辑正文与附件、当前模型/effort/mode/permission profile。
- Produces: fork 新任务后发送、记录运行时偏好并跳转；桌面与窄屏共享同一可操作编辑器。

- [x] **Step 1: 更新失败接线测试**

断言页面提供 fork 编辑动作、ChatView 传入 `beforeTurnId`，且不再从编辑入口调用 rollback；断言移动端依赖原生 textarea/button，不存在隐藏或仅桌面断点类。

- [x] **Step 2: 实现最小页面接线**

页面 fork 后调用现有 `sendTurnInThread`，成功后保存父任务引用、运行时偏好并跳转；ChatView 不再本地裁剪原 thread。

- [x] **Step 3: 运行编辑相关回归**

```bash
CODEX_HOME=/volume2/SSD/codex/Temp/codex-dev-home npx vitest run src/codex-web/tests/edit-message-fork.test.ts src/codex-web/tests/latest-user-message-edit.test.ts src/codex-web/tests/app-server-message-edit-wiring.test.ts
```

Expected: PASS。

### Task 3: 完整验证与记录

**Files:**
- Modify: `docs/exec-plans/active/2026-08-31-edit-message-paginated-fork.md`
- Modify: `docs/handover/2026-07-22-edit-latest-user-message.md`

**Interfaces:**
- Consumes: 隔离测试环境、桌面与 390px 移动 viewport。
- Produces: 测试结果、正反例 Smoke Ledger、剩余兼容风险。

- [x] **Step 1: 运行全量测试和构建**

```bash
CODEX_HOME=/volume2/SSD/codex/Temp/codex-dev-home npm run test
CODEX_HOME=/volume2/SSD/codex/Temp/codex-dev-home npm run build
```

Expected: typecheck、Vitest 和生产构建通过。

- [x] **Step 2: 启动隔离生产服务并验证桌面/移动端**

验证编辑器可打开、内容可修改、取消可恢复、发送触发 fork 后新任务发送；移动端宽度 390px 无横向溢出、按钮可点击、输入不被遮挡。

- [x] **Step 3: 记录反例并停止服务**

验证普通发送不触发 fork、fork 失败不发送、发送失败不跳转；关闭测试页面并停止服务。

## Smoke Ledger

| 路径 | 预期 | 状态 | 证据 |
|---|---|---|---|
| 分页会话编辑发送 | `thread/fork.beforeTurnId` 后在新任务发送 | 通过 | 子 thread `01a05644-750b-7560-8342-1d6b54d5d8c1` 排除被编辑 turn；新 turn `01a05644-df50-7651-ad69-fb0c313bda60` 完成 |
| fork 失败 | 不发送、不跳转，编辑内容保留 | 通过 | 编排单元测试 |
| 发送失败 | 不跳转，显示失败提示 | 通过 | 编排单元测试与 ChatView 异常接线 |
| 普通发送反例 | 不触发编辑 fork | 通过（代码接线） | 普通 `appServerSend` 保持独立，只有编辑回调调用 fork |
| 桌面端编辑发送 | 编辑、发送、跳转和回答完成 | 通过 | `npm run start` + Playwright 1280×720；新 thread `01a05659-f257-7792-b9f4-5ba103df7216` 返回“桌面编辑发送正常” |
| 移动端编辑发送 | 390px 下可编辑、取消、发送且无溢出 | 通过 | Playwright 390×844；新 thread `01a0565c-8943-75f2-8f93-db105b1f11f6` 返回“移动端编辑发送正常”；textarea 与按钮不重叠，document width=390 |
| 移动端取消反例 | 不发送、不跳转、恢复原文 | 通过 | 编辑器关闭，原文保留，取消文本未出现，URL 不变 |
| 全量回归 | typecheck 与 unit 全部通过 | 通过 | 199 个文件、985 项测试 |
| 生产构建 | Next.js 构建成功 | 通过 | 30 个页面生成成功 |
| 基础 app-server smoke | 隔离 bridge、模型和账号来源正常 | 通过 | models=5，accountSource=`app-server.account/read` |

## 状态总览

- 当前状态：功能代码、测试、构建、真实 app-server 协议 smoke 与桌面/移动端 Playwright 验证完成
- 完成状态词：`Code complete`、`Tests pass`、`Smoke passed`

## 决策日志

- 2026-08-31：确认 `codex-cli 0.151.0` 的分页 thread 明确拒绝 `thread/rollback`。
- 2026-08-31：采用当前官方 TUI 的 source-preserving branch 语义，通过实验 API `thread/fork.beforeTurnId` 编辑旧问题。
- 2026-08-31：桌面和移动端继续共享 `MessageItem` 编辑器，移动端通过真实 viewport 验收，不复制实现。
- 2026-08-31：使用 `npm run start` 完成 1280×720 与 390×844 Playwright 正例和取消反例；仅观察到既存 `/api/settings/workspace` 404，与编辑发送链路无关。
