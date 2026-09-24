# 174 Thread/Run 首字延迟优化灰度记录

日期：2026-09-25。分支 `perf/thread-run-latency-174`，基线 develop `6d28480c`。仅更新 174 的 API 与 3 个 Worker，3301/3501 Web、quality-sync、otel-collector、`axis-worker-for-173` 均未改。174 使用 root SSH，部署镜像前校验 commit label 与现有镜像祖先关系；每次重建前检查 queued/provisioning/running/waiting_approval/cancelling Run 为 0。现行 overlay 备份在 174 `/data/agent-studio/docker-compose/compose.deepagents-174.yaml.bak-perf-b12e5550`，回滚版本 `develop-20260924-d9eec75b`。

## 灰度一：终态 context window 请求跳过（已否决、回滚）

提交 `06113e23` 令 resumed Claude Run 的成功终态不再等待 `get_context_usage()`；174 镜像 `perf-20260925-06113e23` 确实把最后文本至 `runtime.result` 从约 0.8 s 缩到约 70 ms。但它把本来可用的精确 `context.window.observed` 记为 `context.window.unavailable`，损失窗口 headroom/rebase/压缩预警语义。已将 174 回滚到 `develop-20260924-d9eec75b`，分支以 `3824a382` 显式 revert。此方向须设计真正异步且可靠落库的后终态观测，不能用假 unavailable 取巧。

## 观测校正与阶段拆分

Langfuse Worker trace 实际存在。根 trace 名 `agent-run`，`harness.worker.run` 是子 observation；之前按根 trace 名 `harness.worker.run` 检索导致误判。174 样本 `run_d2f24587b14c468683d23e1d05fd3f7a` 的 trace ID `02cc97738e567b9e3b4649ee38927f10` 有 16 个 observations。

提交 `753932e2` 仅增加 SDK connect/query send/first message wait 的完成 span，不拆 SDK 生命周期、不改上下文治理。灰度镜像 `perf-20260925-753932e2`，5 条 trace：

| 阶段 | 中位数 | 范围 |
|---|---:|---:|
| `harness.agent.assets.stage` | 303 ms | 249–1,243 ms |
| `harness.mcp.resolve` | 247 ms | 211–311 ms |
| `harness.sdk.connect` | **1,418 ms** | **1,388–1,498 ms** |
| `harness.sdk.query.send` | 0 ms | 0–1 ms |
| `harness.sdk.first_message_wait` | 140 ms | 104–189 ms |

这证实 1.3–1.5 s 固定开销主要在每 Run Claude SDK client connect/CLI 启动。`harness.model.run` 包含 SDK 生命周期，不等于纯模型 API；跨 Run 缓存 ClaudeSDKClient 会携带 cwd、hooks、MCP 凭据、权限和会话态，不能直接共享。

## 灰度二：避免 Worker 与 Runtime 重复 Skill materialization（当前部署）

提交 `b12e5550`：Worker 已将版本固定的 Skills 放到本 Run workspace 后，通过 `RuntimeContext.agent_assets_staged` 告知 Claude/DeepAgents runtime，不再二次删除并重建 `.claude/skills`；独立 Runtime 调用仍保留原 materialize 路径。174 镜像 `perf-20260925-b12e5550`（API/Worker×3 均健康）。

测试：`tests/unit/runtime/test_staged_agent_assets.py` 确认已 staged 的 Skill 树不被重建、未 staged 仍正确 materialize；广泛 runtime/worker/integration 回归 **491 passed**，ruff 通过。174 真实 Run `run_3abf9d729ed34e50a18c1be861acbd6d` 仍有 `agent.assets.staged`、精确 `context.window.observed` 和 `run.succeeded`。

A/B：同一 `lead-agent@1.0.3+platform.c95a6965`、同一提示词 `你好，请只回复 OK`、同一用户、174:8800 直连 API；每组 2 次 warmup＋20 次正式 Run，按时间顺序执行，不是随机交错对照。旧版基线为回滚后的 `develop-20260924-d9eec75b`（与 `753932e2` 仅相差只读诊断 span），新版为 `b12e5550`。原始样本和 Run ID 已保存：

- [旧版基线 JSON](perf-thread-run-174-baseline-20260925.json)。
- [新版优化 JSON](perf-thread-run-174-optimized-20260925.json)。

| 指标 | 旧版 p50 | 旧版 p95 | 新版 p50 | 新版 p95 |
|---|---:|---:|---:|---:|
| 首个 SSE 文本 | 3,272 ms | 4,576 ms | **3,063 ms** | **3,707 ms** |
| Run 总耗时 | 4,233 ms | 5,521 ms | **4,022 ms** | **4,770 ms** |
| 首 SSE 事件 | 55 ms | 77 ms | 54 ms | 79 ms |

Langfuse 三条新版样本中 `harness.mcp.resolve` 为 11–14 ms（旧版五条为 211–311 ms），与移除重复 materialization 的机制一致；`harness.sdk.connect` 仍为约 1.4–1.6 s。客户端首字 p95 改善约 0.87 s，但含模型和资产冷暖波动，不能全部归因于代码变更；需更多交错样本才能量化稳定收益。该灰度没有更改 Run/Thread/Artifact/审批语义。

## 剩余工作

1. 对 SDK `connect` 的 1.4 s 研究安全复用或预热边界：Run 级 hooks、cwd、权限、MCP 凭据和 session store 不能跨 Run 泄漏；先做 SDK 能力验证与 fencing/会话所有权设计，不直接建全局进程池。
2. 对冷资产 staging 1.2 s 长尾做版本级只读缓存/完整性校验实验，禁止把可变 Run workspace 直接挂到共享 cache。
3. 补浏览器首字 Performance，当前 p95 是 API SSE 首文本，不包含 Next/React。
4. 多样本交错 A/B、工具/附件/多角色回归和失败注入，之后再判断是否合入 develop。
