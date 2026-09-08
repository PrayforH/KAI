# 智能体对话预览与构建助手分离

日期：2026-09-07。状态：已部署并完成线上页面回归。Release：`20260907-190533`，Web 主题补丁 `theme1`。

## 改动

1. 执行事实与回答质量分开。前端移除固定五阶段流程，回答旁只展示运行状态及可展开的真实执行详情。兼容 API 保留五项 `loop`，其中“计划”改为“运行准备”，“修正”改为“异常记录”；成功结束不会推断错误已修复或回答经过独立验证。
2. 主区域增加“配置 / 对话预览”。预览原样提交测试问题，不调用构建意图模型；右侧构建助手继续负责理解要求、追问、修改预览和明确的重新试跑。
3. 连续预览通过 `continueFromRunId` 复用同一预览 Session，保留运行时会话及已有的工作区恢复机制，同时携带主对话使用的 `conversation_prompts` 回退上下文。校验用户、草稿、修订、编译快照和上一轮终态；配置变化后另开会话。手动“新对话”和“重新试跑”也另开会话。
4. 预览复用主对话的 Markdown 渲染、表格/代码展示、ActivitySummary、附件上传适配器和输入基础样式。服务端最终回答投影与主对话共用 `harness.agui.response`。原始事件默认折叠，审批及交付文件保留独立入口。
5. “改进这次回答”把选中轮次的原始任务、回答、修订、状态及有长度限制的工具证据提供给构建助手。“应用并重新试跑”回放该轮原始任务及附件；未应用建议、并发运行及草稿冲突仍会阻止误操作。
6. 关闭构建助手或切换配置页不会清空预览；阅读前文时不强制滚动到底部。中文输入法防误发、请求失败保留预览输入。

## 验证

- 前端：79 个文件、514 项 Vitest 测试通过；TypeScript 与 Next.js 生产构建通过。
- 后端：Studio API、试跑状态投影、AG-UI 单元/集成及输入附件接口共 125 项测试通过；有 4 条现有 BinaryInputContent 弃用提示。相关源码 Ruff 检查通过。
- 新增覆盖：连续会话及原始输入、幂等重放、显式新会话、旧修订和运行中续接拒绝、跨用户/草稿隔离、附件归属、直接预览不调用构建模型、失败保留输入、选中旧案例后修改并重新试跑。
- 本地 Chrome 使用真实页面与模拟业务 API 验证 Markdown 表格、连续追问、折叠详情、选中回答反馈、关闭助手、配置/预览切换、显式新会话、深浅主题；无页面异常。
- 174 的前后端镜像在目标机器构建。127.0.0.1:3599 临时 Web 健康检查通过，首页、登录、智能体页、认证配置和运行配置接口均为 HTTP 200。

- 首次正式切换前活动 Run 为 0；API、三个 Worker、quality-sync 和 Web 共六个服务均 healthy、restart 0。正式 OpenAPI 已确认连续预览、附件与 activity 协议。

- 最终线上 Chrome 页面回归通过：连续预览、选中回答反馈、折叠详情、切页保留、新会话、深色文字对比度 ≥ 4.5；另验证分栏拖动、键盘最小/最大宽度、双击复位、宽度持久化、1200px 中间区域、390px 手机输入及主任务右栏。以上使用模拟业务 API 与正式站点资源，没有发起真实业务任务。
- 最终六个服务均 healthy、restart 0，8800 healthz 为 ok；部署后的四个后端文件 SHA256 与本地一致。主题补丁源文件保存在 `theme-fix.tgz`，部署脚本为 `deploy-theme.sh`。

## 实现边界

预览和构建聊天当前保存在页面状态，整页刷新后不自动恢复。自动意图路由和 Worker 测试使用测试模型/内存容器，浏览器使用模拟业务 API；不等价于生产模型的回答质量或长会话效果验收。

部分运行时仍将进度和最终回答混用 message.delta，因此本次复用主对话的事件边界投影，没有宣称已消除所有运行时的中间文本重分类。执行详情保留原始事件供检查。

## 发布与回滚

- 地址：<http://172.20.109.174:3501/studio/agents>。
- 源码与脚本：`/data/kai-preview-20260907-190533`。
- API：`kai/axis-api:20260907-190533`，镜像 ID `sha256:c23a9fda0fae78db6e2857613871c7152aa1a03004b69b3e3ac7250b7d74ae0c`。
- Web：`kai/axis-web:20260907-190533-theme1`，镜像 ID `sha256:737f61b7b6f02425e61ab035409da5ab49554a9a9419f6ac62c781348acc5497`。主题复核发现浅色 fallback 导致深色背景下白字难辨，已改用现有 Studio/Codex 主题变量，单独补发 Web，后端不重复切换。
- 后端基于当前 `20260907-182926` 镜像，仅更新 Studio API、try_run、AG-UI routes 和新增 response 模块。已比对在运行版本源码，差异限定于本次实现。
- 保留 `axis-web-20260907-182926` 及前后端旧镜像。部署脚本失败自动恢复旧 Web 和旧 Compose 组合；无数据库迁移。

如需回滚，先确认没有活动运行，然后：

```sh
docker stop axis-web-20260907-190533-theme1
docker start axis-web-20260907-182926
cd /data/agent-studio/docker-compose
docker compose --profile observability --env-file .env.production \
  -p agent-studio-174 -f compose.yaml -f compose.harbor.yaml \
  -f compose.axis-release-20260906-132229.yaml \
  -f compose.axis-unmetered-20260907-115304.yaml \
  -f compose.axis-builder-20260907-172252.yaml \
  -f compose.axis-intent-20260907-182926.yaml \
  up -d --no-deps --no-build --wait --scale worker=3 api worker quality-sync
```
