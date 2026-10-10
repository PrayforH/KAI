# 174 用户原文记忆提取恢复

2026-10-10 恢复 174 的后台候选提取。发布配置与回滚快照保存在服务器 `/data/kai-memory-restore-20261010/`；其中包含凭据的文件权限为 0600，不提交版本库。

## 故障与修复

舆情分析智能体 `public-opinion-agent` 的成功任务 `run_3517a0ee762b4d6dbb41f8284ec947f9` 收到用户原文：

> 后面的输出统一以带图表的 html 格式报告输出

智能体调用 `mcp__harness-memory__propose_memory` 时，会话的信任标记为 `untrusted`，被 `untrusted-memory-deny` 拒绝。信任标记来自该会话较早的舆情查询工具结果。任务成功完成不代表记忆写入成功。

9 月记忆发布覆盖文件配置了自动提取；后续运行中的 API 和三个 worker 均未携带提取参数，关闭了原本用于从用户原文独立提取的后台通道。因此本轮既没有候选，也没有提取任务。基础 Compose 此前也没有传递这五个配置变量。

修复将 `HARNESS_MEMORY_EXTRACTION_ENABLED/SINCE/BASE_URL/API_KEY/MODEL` 接入基础 Compose 的 API、worker 环境，并补充环境变量示例和部署回归测试。默认仍关闭，启用时需显式配置完整路由、凭据和带时区的上线起点。凭据由服务器原记忆发布配置恢复，不复制进仓库。

174 API 和三个 worker 已重建生效，沿用原镜像及其余运行参数。提取模型使用已验证的 `deepseek-v4-flash`。恢复起点为 `2026-10-10T03:38:08.982985+00:00`（北京时间 11:38:08），仅自动对账起点后的成功任务。单独补入上述指定任务的标准持久提取队列，保留原始租户、用户、智能体 owner/name、会话和任务来源。

`untrusted-memory-deny`、会话信任状态及记忆确认策略保持原值。后台提取器只接收原始用户 prompt 和同范围已有记忆，不读取助手回答或工具结果。原有 166 条取消的提取任务未重放。本轮没有改动 173 配置。

## 验收

- 部署资产及记忆模块单元测试：36 项通过；`git diff --check` 通过。
- 真实提取模型路由预检：从上述用户原文提取出偏好，证据逐字来自用户输入。
- 持久后台处理：指定任务 `completed`，尝试 1 次，错误为空。
- 候选 `memory_3af06f3639c640a093abf0e5d42b8ed0`：`pending`，内容为“用户要求后续输出统一以带图表的 HTML 格式报告输出。”原文证据、来源 run/session、提取任务 ID 及尝试版本完整。
- 用户自动保存授权仍关闭；候选未进入有效记忆召回，需在记忆设置页确认后生效。
- 会话信任仍为 `untrusted`。数据库任务统计为 166 cancelled、1 completed。
- API、三个 worker、当前 Web 均 running/healthy，重启计数 0；HTTPS 首页 200，API `/healthz` 返回 ok；语音并发配置仍为 32。
- 切换前正在运行的任务和实时语音连接均为 0。未发起真实用户对话或浏览器麦克风录音验收；本次验证聚焦配置和后台记忆处理。

## 持久配置与恢复

当前完整启动定义：`/data/kai-memory-restore-20261010/compose.private.json`。

当前生成的 release JSON、`/data/agent-studio/docker-compose/compose.deepagents-174.yaml`、`/data/kai-voice-20261008/compose.voice.yaml` 和正式 `.env.production` 已同步提取配置，避免后续重建再次丢失。启动配置的 API/worker 环境与切换前逐项比较，只有上述五个提取参数变化。

故障隔离时可用当前完整启动定义将提取开关置 false 后，只重建 API 和 worker。无需恢复数据库或改变用户授权；已生成的待确认候选保留。服务器 `before-*` 快照和 `prepared.json` 记录了修改前文件与对应路径，回滚时不得覆盖后续发布的其他配置。

记忆入口：https://172.20.109.174/settings/memory 。选择“舆情分析”智能体，刷新并确认候选后才用于后续跨会话召回。
