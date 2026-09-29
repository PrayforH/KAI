# 174 流式正文被隐藏修复（2026-09-29）

## 问题与原因

用户报告 develop 合并部署后，Web 只显示处理行和空白，停顿后整段正文一起出现。此次只修改和部署 174；173 有独立迭代，未做任何修改或发布。

真实 174 SSE 验收 Run `run_c48b9da0c6c44c25bd26a3e652b61fe1`：77 个 `TEXT_MESSAGE_CONTENT` 分片，工具后的正文在约 5751–9780 ms 连续发送，Run 于 11310 ms 结束。重放到 HarnessHttpAgent 和真实 React 对话组件后，流式 store 与 DOM 均持续增长。

根因是合并后的组件与旧样式不匹配。正文已统一为 `AssistantResponse` / `.assistant-answer`，但 `styles.css` 仍保留：

```css
.harness-assistant-message[data-direct-stream="true"]
  .assistant-answer:not(.live-assistant-response) { display: none; }
```

该规则原本隐藏双组件方案中的重复正文，现在把唯一的正文隐藏。运行结束后仍隐藏，直到 durable history 接管、`data-direct-stream` 清除才整段出现。不是 SSE 缓冲，也不是模型等到结束才输出。旧 DOM 测试只检查 textContent 和节点稳定性，没有加载生产 CSS，因而遗漏了可见性回归。

## 修复与验证

源码提交 `d409e795e296f94b8ce878e8ed490494d37d6afe`：移除旧隐藏规则；将正文悬停控制栏的旧 `.live-assistant-response` 选择器改为当前 `.assistant-answer`。保留统一正文节点和既有交接、取消、过程展示逻辑。

- 新增包含完整生产 styles.css 的组件回归：旧代码明确因 computed display 为 none 失败；修复后首段、增量、完成阶段均可见，且沿用同一个 DOM 节点。
- 8 个相关测试文件、93 项测试通过，覆盖 live store、工具边界、AG-UI、durable handoff、正文和布局。
- Next 16.3.3 Webpack 生产构建及 TypeScript 检查通过，`git diff --check` 通过。
- Headless Chrome 加载旧线上实际 CSS 验证：流式三个阶段和完成阶段均 display:none / height:0，history 阶段才恢复 block。
- 同一浏览器验证加载 canary 与更新后的两个线上入口 CSS：首段、增量、完成、history 全部 display:block，正文具有非零高度。该浏览器检查验证真实发布样式与统一正文 DOM 的可见性；SSE 到完整 React 的路径另用记录重放验证，没有把合成 DOM 检查宣称为真实登录端到端操作。

脱敏浏览器结果与 SSE 时间摘要见同名 JSON。

## 部署

174 的 3301、3501 两个 Web 更新为 `kai/axis-web:stream-fix-d409e795`，image ID `sha256:57059f9e1037920b97330bd4b939a8c921413c84372d5a53ab73f5414df9de4b`，BUILD_ID `xb_tbaQP7TzCnrePeDjt3`。均 healthy，首页 HTTP 200；API health 200。比较发布前后的 API / 三 Worker 容器 ID 和镜像，完全一致。

部署目录 `/data/releases/stream-fix-d409e795/`，backup 保存原 compose overlay 和两个 Web 的容器配置（受限目录，不进 Git）。3301 仅重建 compose web 服务；3501 由 `axis-web-develop-e7b2250f` 切换为 `axis-web-stream-d409e795`，原容器停止保留。临时 canary 已清理。

回滚时恢复 backup/compose.deepagents-174.yaml 至原目录，使用原三个 compose 文件只重建 web；3501 停止新容器并启动 `axis-web-develop-e7b2250f`。API、Worker、数据库无需操作。浏览器已打开的旧页面需刷新以加载新构建的 CSS。
