# 技能搜索与执行过程重复修复（173:3302，2026-09-25）

代码提交：`a403d537898e695bf7fb3d7859c640327b0ed8a1`。

## 发现

- 173 API 的平台技能目录有 21 包，包含 `pptx-generator`，显示名为“PowerPoint 演示（MiniMax）”，且配套 PPT 技能也在。不是镜像漏包。
- 技能仓库页原搜索把整个输入作为连续子串匹配，而 `MiniMax`、`PPT` 分布在显示名、标签和上游来源中；Agent 编辑器平台技能选择器有另一份同类搜索逻辑。
- 真实 Run `run_bf07d659a28e44ecbbca37c05c2a5f34` 的事件里，每次工具结束后可见 `runtime.system`、`message.start` 进入下一轮。持久历史把 `harness_run_activity` tool-call 放在 assistant 回答消息的 content 中；原前端在回答前已有 `TurnActivity`，又可能在 `AssistantMessage.Content` 中把 tool-call 绘成末尾的第二份执行过程，尤其当前 runView 切到下一 Run 时。

## 修复

- 统一技能仓库页与 Agent 编辑器的平台技能检索：按空格拆词，每个词可以分别匹配技能标识、显示名、简介、标签、上游 source URL。`MiniMax PPT` 可找到 `pptx-generator` 和同源配套技能。
- 历史 activity 从 tool-call 解析后始终由回答前的 `TurnActivity` 展示；tool-call 本身不再渲染第二份底部过程。保留当前/历史 Run 的单份归属和已有过程事件，不删审计信息。

## 验证与部署

- 定向测试 34 通过；前端全量 144 个测试文件通过，926 通过、1 跳过；Next production build 通过。
- 发布前无 queued/running/waiting_approval Run；仅更新 173:3302 Web。API/Worker 容器 ID 未变，3301 未操作。
- Web 镜像 `kai/axis-web:evolution-skill-activity-a403d537`，Build ID `9-vPZxodWTkzbATpgFsHi`；Web/API/Worker healthy，3302 `/`、`/login` 与 8802 `/healthz` 均为 200。
- 173 API 内再次确认 `pptx-generator` 目录条目及 MiniMax 上游 URL。
- 回滚配置：173 `/data/agent-studio-evolution-20260920/backups/skill-activity-a403d537/compose.json`，sha256 `4bd259b0ece2f13f592b940c05733e17860c035bbe2e547869942a7d927fb89a`。

浏览器原会话是 `session_expired`，环境有单设备登录保护；未重新登录挤掉其他设备，因此新版本的登录后页面视觉验收仍未执行。组件回归、生产构建和服务核对不能代替该视觉验收。
