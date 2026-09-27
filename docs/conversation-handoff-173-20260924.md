# 对话完成后闪动修复与 173 验证（2026-09-24）

代码提交 `e6a149eb54f46d96e93f119812f99c33e13820c8`，基于此前动效版 `5627298b`。

## 原因与修复

完成时 `DurableHistorySync` 在同一个历史加载回调中执行 `thread.import(repository)` 和 `liveResponseStore.clear(threadId)`。流式正文由独立节点绘制，导入的持久消息也要经历 React 提交；两者同步切换可造成一帧回答节点消失或重排。恢复运行轮询路径亦如此。修复将 live 清除延后一帧，让持久消息先提交，并在 effect 卸载或新 Run 开始时取消/跳过清除。

上一轮新加的 `.tool-card` / `.agent-card` 入场动画会在持久历史重建节点时重播，且 `animation-fill-mode: both` 覆盖已完成卡片的透明度；本次移除该规则和未命中现有 DOM 的完成动画。执行进度的既有完成自动收起未修改。

## 本地验证

- 定向 `durable-history-sync.spec.tsx` 与 `motion-system.spec.ts`：7 通过。
- 全量前端 Vitest：143 个测试文件通过，922 通过、1 跳过。
- Next.js production build：通过。
- `git diff --check`：通过。

## 173:3302 部署

- 发布前 Web `kai/axis-web:evolution-motion-6d28480c-r2`，API/Worker healthy；queued/running/waiting_approval Run 0 条。
- 仅更新 Web 为 `kai/axis-web:evolution-motion-handoff-e6a149eb`；Linux runtime smoke 与隔离 canary `/login` 200 后切换。
- 新 Web Build ID `i1AN6pY6pklmxyFaOFyZb`，`WEB_SOURCE_REVISION` 为完整修复提交；Web/API/Worker healthy，3302 `/` 与 `/login` 200，8802 `/healthz` 200。
- API/Worker 容器 ID 在切换前后完全相同；3301 未操作。
- 回滚快照：173 `/data/agent-studio-evolution-20260920/backups/motion-handoff-e6a149eb/compose.json`，sha256 `8ca6823339cf1b537e959a7f062b9b3782de0a4691fd3b1330931a21d1c97428`。

## 黑盒边界

浏览器原有 admin 会话出现“账号已在其他窗口或设备登录”，系统为单设备登录。未再次登录挤掉另一端；因此没有完成**新版本真实对话的完成瞬间视频复验**。服务健康、实际镜像/BUILD_ID 和组件级交接时序回归已通过，不将它们冒充为视觉验收通过。
