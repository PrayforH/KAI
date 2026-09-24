# 173 / 174 Langfuse 接入与评分修复

## 最终接入

复用已有 Langfuse 3.161.0：`http://172.20.109.111:20025`，组织 `shdata`，项目 `my-harness`（`cmrjabq140061l908y87itjdu`）。不新建账号，不复制业务数据库，不更换项目密钥。

| 应用 | Trace environment | API / Worker / Web | 质量同步 |
|---|---|---|---|
| 173:3302 | `agent-evolution-173` | OTEL 开启，独立采集器 | 新增独立 quality-sync，连接 evolution 数据库和 Redis |
| 174:3501 | `develop-174` | OTEL 开启，3 个 Worker | quality-sync 从旧版更新到当前应用镜像 |

- 应用以 OTLP/HTTP 发往内部 `http://otel-collector:4318/v1/traces`。
- Collector 使用现有项目 Public / Secret Key 的 Basic Auth，转发至 `/api/public/otel`，带 `x-langfuse-ingestion-version: 4`。
- API / Worker 服务名区分为 `agent-studio-api` / `agent-studio-worker`，内容保持 `redacted`，不启用原始凭据采集。
- Web 配置 `LANGFUSE_BASE_URL`、`LANGFUSE_PROJECT_ID`，运行详情的 Trace 链接可用。
- Collector 内部健康检查端点为 `:13133`。173 的采集端口不对宿主机开放；174 现有宿主机端口保持 loopback 绑定。

## 修复的问题

173 evolution 原配置 `HARNESS_OTEL_ENABLED=false`，没有自己的 Collector、quality-sync 和 Trace 链接。174 已能上报 Trace，但 quality-sync 仍使用旧镜像。

实测 Langfuse 拒绝同时带 `traceId` 和 `sessionId` 的评分请求（HTTP 400，要求恰好一个评分目标）。提交 `d9eec75bf15d0c2741b624af69318419191589cb` 将 Run 评分只关联 Trace，会话关系保留在 Trace 和本地评分记录中，并用会拒绝双目标的 MockTransport 做回归。

官方契约：[Scores data model](https://langfuse.com/docs/evaluation/scores/data-model)。每个评分只能关联 Trace、Observation、Session 或 DatasetRun 中的一个目标。

## 镜像

两边使用相同 Harbor 标签 `develop-20260924-d9eec75b`：

| 镜像（前缀 `harbor.shdata.com:5000/agent-studio/amd64/`） | Manifest digest |
|---|---|
| `agent-studio-api`（API、Worker、quality-sync 共用） | `sha256:86d7a1edb6f6d6ff34a0c72fdcb5ab78f010c8e15452e762bd48a8a438ea2dbc` |
| `agent-studio-web` | `sha256:f850215a665d13f5bccd513b9125bd1e4e79b239547e089d06014ab4cef2720b` |
| `agent-studio-otel-collector` | `sha256:95a573e7c336d42b4fd161ec5c964c03cea5e3136863aa3d58e4a09978fadb29` |

API 镜像基于已发布的 8a71086c，只覆盖上述提交修改的 `harness/quality/langfuse.py`。Web 无代码改动，复用原镜像；Collector 两机原本都是同一个 0.128.0 镜像，统一发布为新标签。

## 验证与恢复

- 质量模块检查：6 通过、1 个需显式开启的 live test 跳过。
- 实际环境检查：两边 API、Worker、Web、quality-sync 健康；Collector 内部健康端点返回 200；Web 首页返回 200，Trace 跳转返回 307，目标项目正确。
- 173 Trace `035ad2793256d301c7895933e9617f1c`：API + Worker 两个 observation；174 Trace `f5926f61848ac87a855774e4a259d131`：API + 三个 Worker 四个 observation，环境标记正确。
- 诊断评分经 exporter 写入和回读成功；另通过各自 Redis 队列提交诊断评分，实际 quality-sync 均在一次尝试后成功，Langfuse 回读一致。临时诊断数据库记录已清理，Langfuse 诊断 Trace 保留用于维护核对。
- 174 历史 5,400 条 `quality_export_unavailable` 失败评分，先备份，再通过 ingestion API 每批 100 条补传。沿用原 score ID，避免重复；只在对应事件确认成功后更新同步状态。全部 5,400 条被接受，无批次错误。
- 未运行浏览器、Zcode 或业务模型测试。

## 持久配置与回滚

- 173 配置：`/data/agent-studio-evolution-20260920/compose.json`、`otel-collector.yaml`。
- 174 配置：`/data/agent-studio/docker-compose/compose.deepagents-174.yaml`、`/data/agent-studio/otel-collector/collector.yaml`。`up-deepagents-174.sh` 已包含 observability profile、Collector 和 quality-sync。
- 174:3501 当前 Web 容器：`axis-web-langfuse-d9eec75b`，环境文件 `/data/releases/langfuse-20260924-174/web.env`；旧 Web 停止保留。3301 旧前端未切换。
- 发布脚本、配置备份保存在各机 `/data/releases/langfuse-20260924-<173|174>/`，目录仅 root 可访问。配置文件可能含密钥，不应复制进仓库。
- 174 评分补传前备份：该目录的 `quality-before-replay.json`，进度记录 `replay.log`。
- `compose.beforefix.json` / `compose.deepagents.beforefix.yaml` 是评分修复镜像前、Langfuse 接入后配置。最初配置另存 `compose.before.json` / `compose.deepagents.before.yaml`。
- 无数据库迁移。回滚应用镜像不会删除已上报的 Trace 或评分。
