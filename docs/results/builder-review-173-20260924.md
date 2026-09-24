# 173 Builder 修复与 Zcode 复验（2026-09-24）

## 部署

- Web：`8e72a52c`；API / Worker：`748420ec`。Web 重新部署后 HTTP 200，三个容器均 healthy。
- 分支：`auto/agent-evolution`。174 与 Codex 运行时未调整。

## 审查结论与修改

1. Zcode QA 分支的产品代码修复是 `3c7be43e`（登录表单显式 POST）；其余提交为诊断脚本、报告与证据。已审查并以 `e74a0792` 纳入，避免尚未 hydration 的原生提交将凭据带入 URL。
2. Builder 应用修改以前先请求两份完整项目导出作差异预览，再保存。现在直接保存；只在主动点击“查看代码差异”时导出，已主动生成的差异可复用。版本校验与修改审阅仍保留。
3. Builder 使用公共 `ActivitySummary` 展示运行状态、扫光、计时与完成状态，位于正文上方。通过独立 data part 承载，不进入正文复制内容，不伪造服务端 Run 详情。流式消息 ID 保持稳定。
4. 导航栏停止自动预取未打开的工作区页面，针对知识库、技能、自动化页面 CSS 未使用 preload 警告；保留正常客户端导航。
5. 新建对话的本地 UUID 在首次运行前没有服务端记录，之前立即加载历史会得到 404。新增用户隔离的本地新会话标记，首次运行前跳过历史请求，服务端开始运行或任务列表确认记录后恢复读取。仅对明确在本地创建的空会话跳过，旧会话及未知来源 ID 仍正常请求。用户给出的 `6cb20efa-69af-4266-b2c4-1d6d695a1ba5` 在 173 数据库中无 binding；无法追溯它最初如何产生。

## 本地验证

- Vitest：142 个测试文件，910 通过、1 跳过。
- Next.js production build（webpack）：通过，包含 TypeScript 检查。
- 关键回归：保存不等待项目导出；主动预览仅导出一次；Builder 流式正文节点稳定、进度不混入正文、终态停止扫光；新会话不请求历史，首次运行后恢复加载。

## 修复前实测范围

Zcode 对 Claude Agent SDK QA 草稿的单次测量：模型建议 9007 ms，项目差异导出 315 ms，保存 390 ms。这个小草稿未复现长时间保存，不能据此否定用户在其他草稿上的延迟。此轮消除了保存前可避免的完整项目导出。

新浏览器会话无 console error / pageerror / 失败请求；出现 4 条 CSS preload 警告，与用户随后提供的文本一致。

## 上线复验

Zcode 本轮 6 项浏览器复验通过。实际 R4b 智能体身份以任务 API / 数据库核对为准，修正了浏览器脚本按相同显示名选中另一 QA 智能体的记录偏差。

复验记录：[精简证据](builder-173-20260924/review-8e72a52c.json)。

- QA 草稿 r4 → r5，WebSearch / WebFetch 已保存且界面勾选一致。
- `builder-apply` 请求 548 ms；点击到检测到新修订 1226 ms（含 1 秒轮询粒度，不等同于纯接口耗时）；`builder-project-diff` 请求 0 次。此为单次 QA 草稿测量。
- 生成中 `phase-running`、扫光 1 处、进度位于正文之前；完成后 `phase-completed`、扫光 0 处，未显示虚假运行详情。
- 静置 12 秒并在智能体 / 自动化页面往返，CSS preload 警告 0、console error 0、pageerror 0、失败请求 0。
- 新建主对话后历史请求 0 次；未发送便刷新，历史请求仍 0 次。首次运行后的恢复亦通过：`evolution-smoke-164806@0.1.0`，服务端 Session 核验为 `claude-agent-sdk`，Run succeeded；刷新后历史 HTTP 200，用户消息与相同助手文本恢复，无 404 / console error / pageerror。
- 登录页面 DOM `form.method=post`。
