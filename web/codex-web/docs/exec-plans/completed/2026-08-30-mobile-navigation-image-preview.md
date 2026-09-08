# 移动端新对话导航与图片灯箱修复实施计划

> **For agentic workers:** 按以下清单逐项实施和验证，不扩展到无关导航或媒体功能。

**目标：** 修复移动端项目铅笔按钮导航失败和输出图片灯箱无法加载，并确认桌面 Web 端不回归。

**架构：** 新对话继续使用现有 Next.js 客户端路由，只在项目快捷入口补齐移动侧栏收口和按钮语义。媒体缓存继续按路径复用 Blob URL，但新 Turn 不再撤销仍被历史消息和灯箱引用的 URL。

**技术栈：** Next.js、React、TypeScript、Vitest、Playwright。

## 全局约束

- 不新增依赖，不修改 app-server 协议和数据来源。
- 所有代码注释、测试说明和文档使用简体中文。
- 默认验证环境使用 `CODEX_HOME=/volume2/SSD/codex/Temp/codex-dev-home`。
- 不执行删除命令，不覆盖用户已有改动。

---

### 任务 1：项目铅笔按钮移动端导航

**文件：**
- 修改：`src/components/layout/ProjectGroupHeader.tsx`
- 修改：`src/components/layout/ChatListPanel.tsx`
- 测试：`src/codex-web/tests/sidebar-new-chat-navigation.test.ts`

- [x] 增加失败断言：项目铅笔按钮必须是 `type="button"`。
- [x] 增加失败断言：项目新对话在紧凑视口中必须于路由切换前关闭侧栏，桌面端保持侧栏打开。
- [x] 移动端项目操作区不依赖 hover，桌面端保留悬停显示。
- [x] 实现最小修改并运行定向测试。

### 任务 2：历史输出图片灯箱 URL 生命周期

**文件：**
- 修改：`src/components/chat/ChatView.tsx`
- 测试：`src/codex-web/tests/app-server-output-media-wiring.test.ts`

- [x] 增加失败断言：新 Turn 不得全量撤销历史消息仍引用的 Blob URL。
- [x] 移除发送前的全量媒体 URL 清理，保留按路径缓存和显式文件刷新能力。
- [x] 运行媒体缓存与输出媒体定向测试。

### 任务 3：完整验证与归档

- [x] 使用隔离 `CODEX_HOME` 运行 `npm run test`。
- [x] 运行 `npm run build`。
- [x] 启动应用，使用移动端和桌面端视口验证项目新对话，并记录图片 URL 生命周期与普通路径反例。
- [x] 检查最终 diff 和工作区状态。
- [x] 将本计划移动到 `docs/exec-plans/completed/`。

## 成功标准

- 移动端点击项目铅笔按钮不会触发表单提交，侧栏关闭后进入对应项目的新对话页。
- 输出图片在发送后续 Turn 后仍可在灯箱中加载。
- 桌面端项目新对话和图片灯箱保持可用。
- 普通无图片消息不新增媒体区域，非项目导航路径不受影响。

## Smoke Ledger

| 场景 | 预期 | 结果 |
| --- | --- | --- |
| 390×844 移动视口点击项目铅笔 | 操作区可点击，进入带 `new` 参数的新对话并关闭侧栏 | 通过；按钮 `type=button`、操作区 `pointer-events=auto`，目录保持 `/home/rrssnas/code/codex-web` |
| 1280×720 桌面视口点击项目铅笔 | 进入新对话但保留桌面侧栏 | 通过；导航后项目铅笔按钮仍在 DOM 中 |
| 新 Turn 后打开历史输出图片 | 历史消息 Blob URL 不被提前撤销，灯箱可继续加载 | URL 生命周期与媒体缓存定向测试通过；浏览器工具在打开真实历史图片会话时因审批服务 503 未完成追加人工点击 |
| 普通导航与无图片路径 | 不改变桌面侧栏行为，不新增媒体状态 | 桌面反例通过；完整 979 个测试用例通过 |

## 验证记录

- `npx vitest run src/codex-web/tests/sidebar-new-chat-navigation.test.ts src/codex-web/tests/app-server-output-media-wiring.test.ts src/lib/tests/media-resource-cache.test.ts`：3 个测试文件、18 个用例通过。
- `npm run test`：198 个测试文件、979 个用例通过。
- `npm run build`：生产构建通过，30 个静态页面生成完成。
- Playwright 当前控制台增量检查：0 个 error、0 个 warning。
