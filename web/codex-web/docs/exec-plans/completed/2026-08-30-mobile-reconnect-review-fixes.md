# 移动端断线重连审查修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复提交 `ded8e4b` 审查发现的三个问题：悬挂连接无法进入失败态、部分页面没有恢复入口、同步门禁可被非输入框发送入口绕过。

**Architecture:** `AppServerProvider` 使用独立 deadline timer 管理 30 秒连接窗口，超时主动关闭 socket 并进入全局 `failed`。连接提示数据由纯函数统一生成，各聊天表面复用同一文案与按钮；所有发送路径在核心函数检查 `composerDisabled`，而不是只依赖输入框 disabled 属性。

**Tech Stack:** TypeScript、React 19、Codex app-server JSON-RPC、Vitest、Playwright MCP。

## Global Constraints

- 测试环境固定为 `CODEX_HOME=/volume2/SSD/codex/Temp/codex-dev-home`。
- 不修改 `/home/rrssnas/code/codex`。
- 不引入第三方依赖，不修改协议 schema，不伪造 Turn 状态。
- 断线、失败和同步期间保留已有消息与 active Turn。
- 不执行删除命令，不自动 push。

---

### Task 1: 独立重连截止计时器

**Files:**
- Modify: `src/codex-web/AppServerProvider.tsx`
- Modify: `src/codex-web/app-server-browser-client.ts`
- Test: `src/codex-web/tests/app-server-reconnect-wiring.test.ts`
- Test: `src/codex-web/tests/app-server-browser-client.test.ts`

**Interfaces:**
- Consumes: `RECONNECT_FAILURE_AFTER_MS`、`AppServerBrowserClient.close()`。
- Produces: 独立于单次请求完成状态的 deadline timer；超时后 `connection.data === "failed"`。

- [x] **Step 1: 写入失败测试**

```ts
expect(provider).toContain("reconnectDeadlineTimer")
expect(provider).toContain("window.setTimeout(failReconnect, RECONNECT_FAILURE_AFTER_MS)")
expect(provider).toContain("client.close()")
```

- [x] **Step 2: 运行测试并确认旧实现缺少独立 timer**

Run: `npx vitest run src/codex-web/tests/app-server-reconnect-wiring.test.ts`

Expected: FAIL，源码中没有 `reconnectDeadlineTimer`。

- [x] **Step 3: 实现最小 deadline 生命周期**

```ts
let reconnectDeadlineTimer: number | null = null;
let reconnectStopped = false;

function failReconnect() {
  reconnectStopped = true;
  client.close();
  setState((current) => ({
    ...current,
    connection: { source: "web-bridge", data: "failed" },
  }));
}
```

在首次 bootstrap、自动重连和手动重连窗口启动 deadline；成功、登出和 Provider 卸载时清理。

- [x] **Step 4: 重跑定向测试**

Run: `npx vitest run src/codex-web/tests/app-server-browser-client.test.ts src/codex-web/tests/app-server-reconnect-wiring.test.ts`

Expected: PASS。

### Task 2: 所有聊天表面提供失败恢复

**Files:**
- Create: `src/codex-web/connection-notice.ts`
- Create: `src/codex-web/tests/connection-notice.test.ts`
- Modify: `src/app/chat/[id]/page.tsx`
- Modify: `src/app/chat/page.tsx`
- Modify: `src/components/layout/AppShell.tsx`
- Modify: `src/components/layout/SplitColumn.tsx`
- Modify: `src/components/layout/WorkspaceSidebar/SideChatPanel.tsx`

**Interfaces:**
- Produces: `appServerConnectionNotice(status, reconnect): ConnectionNotice | null`。
- Consumes: `ConnectionStatus`、Provider `reconnect()` action。

- [x] **Step 1: 为统一 notice 写纯函数测试**

```ts
expect(appServerConnectionNotice("failed", reconnect)).toMatchObject({
  message: "与 Codex app-server 的连接失败。",
  actions: [{ label: "重新连接" }],
});
expect(appServerConnectionNotice("reconnecting", reconnect)?.message)
  .toContain("正在重新连接");
expect(appServerConnectionNotice("connected", reconnect)).toBeNull();
```

- [x] **Step 2: 实现纯 notice 适配器**

```ts
export function appServerConnectionNotice(status: ConnectionStatus, reconnect: () => void) {
  if (status === "failed") return {
    message: "与 Codex app-server 的连接失败。",
    description: "请检查网络或服务状态后重试。",
    actions: [{ label: "重新连接", onClick: reconnect }],
  };
  if (status === "reconnecting") return {
    message: "与 Codex app-server 的连接已断开，正在重新连接...",
  };
  return null;
}
```

- [x] **Step 3: 接入主会话、新会话、分栏、侧聊和非聊天路由**

每个 `ChatView` 传入统一 `appServerNotice` 与 `composerDisabled={connection !== "connected"}`。分栏在失败时保留已有 thread，不替换为整块错误；AppShell 在非聊天路由显示共享失败 banner。

- [x] **Step 4: 运行 notice 与 wiring 测试**

Run: `npx vitest run src/codex-web/tests/connection-notice.test.ts src/codex-web/tests/app-server-reconnect-wiring.test.ts`

Expected: PASS。

### Task 3: 核心发送门禁与完整验证

**Files:**
- Modify: `src/components/chat/ChatView.tsx`
- Modify: `src/codex-web/tests/app-server-reconnect-wiring.test.ts`
- Update: `docs/exec-plans/active/2026-08-30-mobile-reconnect-review-fixes.md`

**Interfaces:**
- Consumes: `composerDisabled: boolean`。
- Produces: 输入框、队列、计划实施、目标操作和核心 `sendMessage` 使用相同门禁。

- [x] **Step 1: 在核心发送函数增加门禁**

```ts
if (composerDisabled || readOnly) return false;
```

同时令 `PlanImplementationPromptBar.disabled` 与 `GoalProgressRow.pending` 包含 `composerDisabled`。

- [x] **Step 2: 补接线测试**

```ts
expect(chatView).toContain("if (composerDisabled || readOnly) return false")
expect(chatView).toContain("disabled={composerDisabled || isStreaming}")
expect(chatView).toContain("pending={goalMutationPending || composerDisabled}")
```

- [x] **Step 3: 运行类型检查与完整测试**

Run: `npm run typecheck && npm run test`

Expected: 197 个以上测试文件全部通过。

- [x] **Step 4: 运行生产构建和真实浏览器反例**

Run: `npm run build`

Expected: Next.js 生产构建通过。真实浏览器验证悬挂/断线失败态、各聊天表面输入禁用、按钮重连和同步后真实模型发送。

- [x] **Step 5: 更新状态、决策日志和 Smoke Ledger**

记录普通路径、触发路径、首次加载失败、分栏、侧聊、计划按钮门禁和真实设备剩余风险。

## Self-Review

- Spec coverage: 三条审查发现分别由 Task 1、Task 2、Task 3 覆盖。
- Placeholder scan: 无 TBD、TODO 或未定义接口。
- Type consistency: notice 使用现有 `ConnectionStatus`；发送门禁复用现有 `composerDisabled`；Provider action 继续使用 `reconnect()`。

## 状态总览

- 当前状态：`Code complete`、`Tests pass`、`Smoke passed`；计划已归档到 `completed/`。
- 完整测试：198 个测试文件、973 项测试通过。
- 生产构建：Next.js 编译、TypeScript、30 个页面路由与 postbuild 通过。

## 决策日志

- 2026-08-30：deadline 使用独立 timer，不再依赖单次连接尝试先返回错误。
- 2026-08-30：deadline 同时 abort bridge URL fetch、关闭 CONNECTING WebSocket、拒绝 pending JSON-RPC，确保按钮能开始新 bootstrap。
- 2026-08-30：连接文案由纯函数统一生成；主历史、新会话、分栏、侧聊和非聊天路由分别接入适合其布局的位置。
- 2026-08-30：`composerDisabled` 在核心发送函数检查，同时覆盖计划、目标编辑和队列，避免 UI 旁路。

## Smoke Ledger

| 场景 | 预期 | 状态 |
|---|---|---|
| bridge URL fetch 永久悬挂 | 约 30 秒后进入 failed | 通过：拦截请求且不返回，失败 UI 与按钮出现 |
| 悬挂失败后手动重连 | 新 bootstrap 不受旧请求占用 | 通过：解除拦截并点击按钮后恢复模型与输入 |
| 新会话失败 | 显示按钮且不能发送 | 通过：失败 UI 出现；恢复后 textbox 与模型重新出现 |
| 分栏失败 | 两列内容保留、两输入禁用、单一恢复入口 | 通过：两列历史标记保留，2/2 textarea 禁用 |
| 分栏手动恢复 | 两列同步完成前保持禁用 | 通过：点击后立即仍禁用，同步后 2/2 恢复 |
| 侧聊无 childThread | 不停留在无恢复错误页 | 通过：侧聊 Sheet 内显示独立重新连接按钮 |
| 非聊天设置页 | AppShell 提供全局恢复入口 | 通过：设置内容保留且按钮可见 |
| 重连后真实模型 | 可正常提交，不进入陈旧 Turn | 通过：模型回复 `REVIEW_FIX_RECONNECT_OK` |
| 新生产页面 console | 无稳定 hydration 回归 | 通过：全新登录并打开设置页为 0 errors |

## 剩余风险

- 当前测试 app-server 的 ephemeral `thread/fork` 返回 `excludeTurns` 能力错误，无法创建真实 side-chat child composer；该表面的连接门禁由 TypeScript、wiring 测试及无 childThread 浏览器反例覆盖。
- iOS/Android 直接回收页面进程仍属于页面重新加载路径，需要依赖 app-server 历史恢复。
