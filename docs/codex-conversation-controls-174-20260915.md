# 174 会话输入与控制按钮统一

## 改动与基准

以项目 Codex Web 的 `MessageInputParts` → `PromptInputSubmit` → `InputGroupButton` 为尺寸基准：`icon-sm` 为 32×32 CSS px，图标为 16×16 CSS px。统一圆形、单色按钮及悬停、按下、键盘焦点、禁用状态，适配深浅主题。

`ConversationControl` 统一发送、停止、暂停和继续图标。主会话（含运行中补充输入时的停止、视频提交）、构建助手、效果测试、Skill 共创、待发送队列共同使用该组件。效果测试的文字方块和构建附件的文字加号改为 SVG，避免受字体影响。队列暂停仍只暂停待发送队列，停止仍调用原有运行取消接口；本次没有新增运行暂停协议。

输入区参照 Codex `MessageInput` 的 `min-h-12 px-4 py-3`，统一最小高度 48 px、水平内边距 16 px、垂直内边距 12 px、14 px 字号；随内容增高至 192 px 后滚动。工具栏统一 32 px 控件和 8 px 内边距。输入框宽度仍随所在面板变化。

## 验证

- 前端全量测试 602 passed、1 skipped；更新原停止按钮绑定旧 CSS 类名和旧图标路径的断言，保留活动运行、终态停止按钮和取消调用检查。
- 本地 Next.js 生产构建通过，实际启动本地生产服务，首页 HTTP 200。
- 174 Linux 镜像构建完成，预发布容器首页 HTTP 200，默认配置为 `1.0.3+platform.99dfe677`，重启次数 0。
- 浏览器自动化连接超时；原生 Chrome 方式随后因用户切换窗口而未完成。此次未计入浏览器截图或逐像素视觉验收。

## 发布与回滚

- 发布目录：`/data/codex-controls-20260915-1930`。
- Web 镜像：`kai/axis-web:codex-controls-20260915-1930`。
- 镜像 ID：`sha256:9399638996a16e1ca3de02d9c03cb5318113b6d2173f1e65f1ec1935ab37b20a`。
- 只重建 `web-integrated`，保留默认 Agent 1.0.3、API/Worker 和 CubeSandbox 配置。
- 当前配置为 `compose.release.private.json`；回滚使用同目录 `compose.previous.private.json` 执行 `docker compose -p agent-studio-174 -f <配置文件> up -d --no-deps web-integrated`。
