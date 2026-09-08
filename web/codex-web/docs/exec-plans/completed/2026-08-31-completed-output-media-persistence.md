# 完成态输出图片持久展示实施计划

> **For agentic workers:** 按清单逐项实施和验证，保持改动集中在完成态消息合并与历史恢复适配器。

**目标：** 让 app-server `imageView` 和 `imageGeneration` 图片在 Turn 完成、页面刷新和灯箱打开后持续可用。

**架构：** app-server notification 生成的完成态富媒体消息是同一 Turn 的最新事实源；append 合并时用它替换先到的历史纯文本消息，但保留原位置。历史 Thread 解析同时承认官方图片 item，并继续复用现有 `turnItemsToMessageContent`、`MediaPreview` 和 Blob 缓存。

**技术栈：** Next.js、React、TypeScript、Vitest、Playwright、Codex app-server。

## 全局约束

- 不新增依赖，不修改 generated schema 和 app-server 协议。
- 图片来源保持为 `app-server.imageView` / `app-server.imageGeneration`。
- 默认验证环境使用 `CODEX_HOME=/volume2/SSD/codex/Temp/codex-dev-home`。
- 不执行删除命令，不处理现有 `.playwright-mcp/` 临时目录。

---

### 任务 1：完成态富媒体消息覆盖同 Turn 旧消息

**文件：**
- 修改：`src/codex-web/thread-turns-page-adapter.ts`
- 测试：`src/codex-web/tests/thread-turns-page-adapter.test.ts`

- [x] 写失败测试：append 合并时最新富媒体 assistant 替换同 Turn 纯文本 assistant。
- [x] 保持消息位置和 prepend 分页语义，实现最小替换逻辑。
- [x] 运行定向测试。

### 任务 2：图片 item 历史恢复

**文件：**
- 修改：`src/codex-web/thread-history-adapter.ts`
- 测试：`src/codex-web/tests/thread-history-adapter.test.ts`

- [x] 写失败测试：历史 `imageView` 生成含媒体的 assistant 消息且不计为 unsupported。
- [x] 将 `imageView`、`imageGeneration` 纳入支持的 assistant item。
- [x] 运行定向测试。

### 任务 3：完整验证与归档

- [x] 完成态媒体显示在默认折叠的处理区域之外，与 streaming 路径一致。
- [x] 运行 `npm run test`。
- [x] 运行 `npm run build`。
- [x] 使用 `npm run start` 验证 Turn 完成、灯箱保持、刷新恢复和无图片反例。
- [x] 关闭浏览器与服务，检查最终 diff。
- [x] 将计划移动到 `docs/exec-plans/completed/`。

## 成功标准

- Turn 完成后输出图片仍在消息中，已打开灯箱不因完成态合并而卸载。
- 刷新会话后图片由完整 app-server Thread item 恢复。
- 同 Turn 只有一条 assistant 消息，原有消息顺序不变。
- 普通文本 Turn、prepend 历史分页和无图片 Turn 行为不变。

## Smoke Ledger

| 场景 | 预期 | 结果 |
| --- | --- | --- |
| 已完成 `imageView` Thread 刷新 | 完整 app-server item 恢复为消息图片 | 通过；4 个历史图片均恢复，原图尺寸均为 `1254×1254` |
| 新 `imageView` Turn 完成 | 图片留在完成态消息且处理区默认收起 | 通过；最新消息 Fiber 保留 `app-server.imageView` 媒体，缩略图显示 `256×256` |
| 完成态图片打开灯箱 | Turn 完成后灯箱不卸载且 Blob URL 有效 | 通过；等待 10 秒后灯箱仍存在，显示 `648×648`，原图 `1254×1254` |
| 普通纯文本 Thread | 不生成媒体容器或输出图片 | 通过；输出图片 0 个，媒体容器 0 个 |
| 浏览器控制台 | 无本次图片加载或 Blob 错误 | 通过；仅有既有 `/api/settings/workspace` 404 |

## 验证记录

- 定向测试：5 个测试文件、67 个用例通过。
- `npm run test`：198 个测试文件、981 个用例通过。
- `npm run build`：生产构建通过，30 个静态页面生成完成。
- `npm run start`：使用隔离 `CODEX_HOME=/volume2/SSD/codex/Temp/codex-dev-home` 完成真实 app-server 验收，浏览器与服务已关闭。
