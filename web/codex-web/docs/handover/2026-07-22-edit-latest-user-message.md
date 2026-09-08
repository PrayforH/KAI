# 最近用户问题编辑功能交接

关联计划：[最近用户问题编辑功能实施计划](../exec-plans/completed/2026-07-22-edit-latest-user-message.md)

## 结论

最近一个已完成回答对应的用户问题可以就地进入编辑器。自 Codex 0.151.0 起，发送编辑内容时按当前官方 TUI 的 source-preserving branch 语义执行：

```text
app-server.thread/fork { threadId, beforeTurnId: 被编辑消息所属 turn }
  -> app-server.turn/start（新 thread）
  -> 跳转新任务
```

原任务和本地文件改动保持不变。分页 thread 明确不支持已废弃的 `thread/rollback`，因此编辑发送会创建 fork，不再修改原 thread 历史。

## 旧版官方 Session 证据

以下证据记录的是 Codex 0.144.6 的历史行为，仅用于解释旧实现来源，不再代表当前产品语义。

对照文件：

- `/volume2/SSD/codex/Temp/codex-session/rollout-2026-07-22T16-46-34-019f8901-564e-7e53-a319-bdbf9734dfc0.jsonl`
- `/volume2/SSD/codex/Temp/codex-session/rollout-2026-07-22T16-46-34-019f8901-564e-7e53-a319-bdbf9734dfc0.new.jsonl`

编辑后的 `.new.jsonl` 在原第二轮完成事件之后新增：

1. `token_count`
2. `thread_rolled_back { num_turns: 1 }`
3. `thread_settings_applied`
4. 新的 `task_started` / user message / assistant message / `task_complete`

session id 保持 `019f8901-564e-7e53-a319-bdbf9734dfc0` 不变。

## 实现边界

- `AppServerProvider.forkThread` 透传实验 API `beforeTurnId`；Web initialize 已声明 `experimentalApi: true`。
- ChatView 只对带真实 `turn_id` 的最后一个已完成用户消息开放编辑，并把该 turn 作为排除边界。
- 页面严格执行 `fork -> turn/start -> navigate`；fork 或发送失败时不跳转，编辑器保留内容并显示错误。
- 新 turn 沿用当前模型、effort、mode、permission profile 和附件持久化链路。
- 最后一个已有助手回答的用户消息才可编辑；生成中、未回答和更早消息没有入口。
- 旧 `rollbackThread` 与 `bridge/sync/threadRollback` 保留供既有兼容路径使用，但编辑入口不再调用它们。
- 普通文件和图片附件会保留并重新发送；历史文件摘录只保存展示元数据，缺少原摘录正文，因此带文件摘录的消息不显示编辑入口，避免静默丢失上下文。

## 升级风险

`thread/fork.beforeTurnId` 当前属于 experimental API。升级 Codex CLI 时必须继续以官方 TUI 和 app-server schema 为准，并保留 initialize 的 `experimentalApi` 能力声明。

## 验证记录

- 定向回归：6 个相关测试文件、27 项通过，包括 WebSocket bridge 集成。
- 全量测试：107 个测试文件、520 项通过，包含 TypeScript typecheck。
- 生产构建：22 个路由构建成功；保留既有 `next.config.mjs` NFT trace warning。
- 基础 smoke：隔离 bridge、7 个模型和 `app-server.account/read` 通过。
- 生产页面：`http://192.168.3.12:3001/chat` 返回 HTTP 200，使用隔离 `CODEX_HOME=/volume2/SSD/codex/Temp/codex-dev-home`。
- 视觉自动化：playwright-mcp 连接项目指定 CDP 时在初始化阶段超时，随后按项目规则停止重试；原始 CDP 单 target 连接正常，并完成 1440x1000 桌面和 390x844 移动端验证。
- 完成态正例：隔离 31 回合 fixture 的当前可见 30 条用户消息中只有 `user-31` 有编辑入口；编辑框预填原文并自动聚焦，取消和 Escape 均恢复原文。
- 生成中反例：停留在“组织回复中”的会话没有编辑入口。
- 布局：桌面和移动端 textarea 均不与取消/发送按钮重叠，页面无横向溢出；操作期间 console 无新增异常。
- 截图：`/volume2/SSD/codex/Temp/codex-web-edit-message-cdp.png`。
- 隔离 fixture：`/volume2/SSD/codex/Temp/codex-dev-home/sessions/2026/07/11/rollout-2026-07-11T15-30-00-199cf227-4c3d-4c0a-ab8a-79d90d2667b8.jsonl`。
- 编辑发送的 `rollback -> turn/start` 由定向自动化覆盖；隔离环境未登录，因此 CDP 没有切换到真实 `CODEX_HOME` 执行实际回答生成。

## 2026-08-31 兼容修复验证

- 环境：`codex-cli 0.151.0`，`CODEX_HOME=/volume2/SSD/codex/Temp/codex-dev-home`。
- 定向回归：3 个文件、11 项通过，覆盖 fork/发送/导航顺序与失败反例。
- 全量测试：199 个文件、985 项通过，包含 TypeScript typecheck。
- 生产构建：30 个页面生成成功。
- 基础 smoke：隔离 bridge 初始化通过，模型数为 5，账号来源为 `app-server.account/read`。
- 真实协议 smoke：源 thread `01a053ce-5d1c-7051-a4a9-c1314f0ffbd5` 在 `beforeTurnId=01a0540a-26fe-78c3-bade-32322ec8134b` 处 fork；子 thread `01a05644-750b-7560-8342-1d6b54d5d8c1` 保留 5 轮且排除被编辑 turn。
- 真实发送 smoke：子 thread 的 turn `01a05644-df50-7651-ad69-fb0c313bda60` 从 `inProgress` 进入 `completed`。
- 移动端代码路径：桌面与移动端共享同一 `MessageItem` textarea/button 和同一 fork 发送回调，没有 viewport 条件分支。
- 桌面 Playwright：`npm run start` 下使用 1280×720 viewport，编辑、发送、跳转和回答完成；新 thread `01a05659-f257-7792-b9f4-5ba103df7216` 返回“桌面编辑发送正常”。
- 移动端 Playwright：使用 390×844 viewport，编辑器宽 358px、textarea 为 326×112，按钮无重叠、页面无横向溢出；新 thread `01a0565c-8943-75f2-8f93-db105b1f11f6` 返回“移动端编辑发送正常”。
- 移动端取消反例：取消后编辑器关闭、原文保留、临时文本未出现、URL 不变。
- Console：仅观察到既存 `/api/settings/workspace` 404，未发现编辑发送相关异常。

## 当前状态

- `Code complete`
- `Tests pass`
- `Smoke passed`
- 未使用真实 `CODEX_HOME`
