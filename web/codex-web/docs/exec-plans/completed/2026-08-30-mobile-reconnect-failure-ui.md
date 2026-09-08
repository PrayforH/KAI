# 移动端断线重连失败态执行计划

**目标：** 移动端浏览器进入后台或网络不佳导致 Web bridge 断开时，保留当前聊天与运行 Turn 内容；短暂断线自动重连，长时间失败后提供明确的手动重连入口。

**架构：** `web-bridge` 连接状态仍由 `AppServerProvider` 统一管理。断线后按既有退避策略重试，连续 30 秒失败后进入 `failed` 并停止自动重试；用户点击“重新连接”后重新执行 bridge URL 解析、initialize 与 thread resume。聊天页在整个过程中保留内存消息和 active Turn，只禁用 composer、队列与状态变更操作。

**技术栈：** TypeScript、React 19、Codex app-server JSON-RPC、Vitest、Playwright MCP、真实 Codex 模型。

## 全局约束

- 测试使用 `CODEX_HOME=/volume2/SSD/codex/Temp/codex-dev-home`。
- app-server notification、`thread/resume` 和历史分页继续作为状态事实源。
- 不修改官方 `/home/rrssnas/code/codex`。
- 不引入依赖，不持久化 OAuth/API 凭据，不伪造 Turn 终态。

## 执行清单

### Task 1：保留断线期间页面状态

- [x] 连接变化时不再清空已加载消息。
- [x] 保留缓存中的 active Turn、推理内容和部分流式输出。
- [x] 断线时显示“正在重新连接”，不再重复显示“只读历史会话”。
- [x] 断线期间禁用输入框、发送按钮、队列发送和中断操作。

### Task 2：增加长时间失败态与手动重连

- [x] 保留 250ms、500ms、1s、2s、5s 封顶的退避策略。
- [x] 连续 30 秒失败后进入 `failed` 并停止自动重试。
- [x] 失败 UI 显示错误说明和“重新连接”按钮。
- [x] 手动重连重新开启一轮 30 秒自动重试。
- [x] Web 登录失效仍停止重试并跳转登录页。

### Task 3：重连后的线程同步门禁

- [x] bridge 恢复后先执行当前线程 read/resume/turns 同步。
- [x] 同步完成前保持 composer 禁用。
- [x] 同步期间暂停队列自动发送，避免消息进入陈旧 Turn。
- [x] 同步完成后恢复输入，并验证真实模型可继续回复。

### Task 4：验证与归档

- [x] 运行重连策略、Provider 接线和 active-writer 定向测试。
- [x] 运行完整 `npm run test`。
- [x] 运行 `npm run build`。
- [x] 使用移动端 viewport 和真实模型执行断线、失败、手动恢复反例。
- [x] 清理 Playwright 日志与截图，不让临时产物进入 Git。

## 状态总览

- 当前状态：`Code complete`、`Tests pass`、`Smoke passed`。
- 未执行发布或推送，因此不是 `Release ready` 或 `Shipped`。

## 决策日志

- 2026-08-30：30 秒前保持自动重连，避免短暂网络抖动过早要求用户介入；30 秒后停止后台重试并提供明确按钮。
- 2026-08-30：`failed` 只改变连接 UI 和操作可用性，不替换 ChatView，也不清空消息或 active Turn。
- 2026-08-30：手动重连成功不等于线程已同步；增加 `sessionSyncing` 门禁，等待 `thread/resume` 与历史同步完成后再恢复输入。
- 2026-08-30：队列发送同样受 composer 门禁控制，避免断线或恢复竞态期间自动提交。
- 2026-08-30：官方 TUI 对 app-server event stream 断开采用错误退出；Web 端为保留浏览器页面，采用可恢复失败态，不照搬退出行为。

## Smoke Ledger

| 场景 | 预期 | 状态 |
|---|---|---|
| 普通连接与多轮对话 | 消息和真实模型回复正常 | 通过：三轮对话及重连后追加消息均成功 |
| 浏览器后台冻结后恢复 | 页面内容不清空 | 通过：冻结前后消息长度和标记计数一致 |
| 流式输出过程中断线 | 已输出内容保持，输入禁用 | 通过：`STREAM_KEEP_0001` 至约 `0045` 保留 |
| 短暂断线 | 显示自动重连提示，不显示只读历史提示 | 通过：真实 bridge 停止验证 |
| 连续 30 秒失败 | 停止自动重试，显示失败说明和按钮 | 通过：按钮可见、历史保留、输入禁用 |
| 服务恢复但未点击按钮 | 页面继续保持失败态 | 通过：等待 3 秒后仍为 failed |
| 点击重新连接 | 重新 initialize，并等待线程同步 | 通过：点击后输入立即保持禁用，同步后恢复 |
| 重连后真实模型 | 可继续发送，不进入陈旧 Turn | 通过：模型回复 `SYNCED_MANUAL_RECONNECT_OK` |
| active-writer 冲突 | 仍进入只读回放 | 通过：定向 wiring 测试 |
| 全量回归 | 类型和单元测试无回归 | 通过：197 files / 967 tests |
| 生产构建 | Next.js 构建可用 | 通过：30 个页面路由生成完成 |

## 验证记录

- `npm run test`：通过，197 个测试文件、967 项测试。
- `npx vitest run src/codex-web/tests/active-writer-recovery.test.ts src/codex-web/tests/reconnect-policy.test.ts src/codex-web/tests/app-server-reconnect-wiring.test.ts`：通过，3 个测试文件、12 项测试。
- `npm run build`：通过，Next.js 编译、TypeScript、30 个页面路由和 postbuild 完成。
- 真实浏览器：390×844 viewport，真实 app-server 与真实模型；断线内容保留、30 秒失败、手动重连和同步后发送均通过。
- console：断线期间仅出现预期 `ERR_CONNECTION_REFUSED`；另有既存 `/api/settings/workspace` 404 噪声，本次未修改该接口。
- `git diff --check`：通过。

## 剩余风险

- Headless Chromium 无法完全模拟 iOS/Android 长时间后台后直接回收浏览器进程；真实设备进程被回收时属于页面重新加载，需要依赖 app-server 历史恢复。
