# Session 级 SDK 连接复用：第一阶段实现

日期：2026-09-29。分支：`auto/session-runtime-reuse`，基线 `7ea54db6`。

## 交付状态

已完成运行时代码、关闭开关时的兼容路径、故障与跨轮隔离测试，以及 173 演化栈容器中的真实 CLI 对照实验。**尚未发布到线上 Worker；开关默认关闭。** 173/174 的服务镜像、Compose 和环境变量均未改动。服务器实验在独立 Python 进程中临时加载新模块；没有调用外部模型，也没有读写线上会话数据库。

## 实现

- 保留每条输入对应的持久 Run、RunEvent、取消、审批和 Artifact。新增 Worker 内的有界 `WarmSdkPool`，按 tenant/user/session 持有连接。
- 正常成功完成一轮后保留 SDK/CLI；下一轮使用新 RunBinding。hooks、SDK MCP、SessionStore 和 stderr 使用分发器；实际异步回调任务进入新 Run 的 ContextVars，包括身份、文件发布器和 trace。
- CLI 有独立临时控制目录，只 materialize 固定版本 Skill 资产。业务文件、沙箱 executor、工作目录恢复/归档仍按 Run；没有引入跨 Run 物理沙箱复用。
- 每次借用检查配置/权限/凭据/工具 schema 指纹、原生 session ID、Postgres transcript revision。另一 Worker 写过历史后，旧连接强制销毁；workspace_snapshot_id 的正常变化不导致误失效。
- 单一永久响应 reader 分发当前 Run 消息；idle 迟到消息、镜像异常、取消、协议错误、context 控制失败、工具后台任务等均阻止连接再次复用。已经尝试发送的 warm query 出错后不自动重放。
- Session gate 续租失败会主动取消执行；SDK 发请求和回调时核验 Redis 所有权，输出使用单调时钟检查租约期限。避免暂停超过 TTL 的 Worker 在心跳恢复前继续输出。
- 空闲 TTL、池容量和淘汰负责资源回收；容器关闭时主动 disconnect。镜像更新或进程重启后走持久 SessionStore 冷恢复。

第一阶段复用范围：Worker 内运行 CLI、业务文件工具经沙箱 MCP 代理、具备平台 hooks 和带 revision 的 SessionStore。原生本地文件工具、远程 CLI transport、配置为后台执行的子 Agent、无所有权凭据或不支持的 MCP/SDK 配置走冷路径。发生子任务消息的 Run 可正常执行，但结束后不保留连接。

## 173 真实 CLI 对照实验

环境：运行中容器 `agent-evolution-173-worker`；原镜像 `kai/axis-api:context-compaction-20260928-r6`；SDK 0.2.152。模型端点是实验进程在 localhost 启动的固定响应服务，每轮要求真实 CLI 调用真实 in-process MCP 工具，再返回 OK。工具写入本轮临时目录，并核对 hook、MCP 的 Run 上下文。

脚本：[probe_warm_sdk.py](../scripts/probe_warm_sdk.py)。

原始数据：[30 轮对照](results/sdk-warm-probe-173-20260929.json)。每组共 31 轮，首轮单独作为预热，其余 30 轮用于下表；所有样本成功，warm 后续 30/30 命中。每组 31 次 hook 与 31 次 MCP 调用全部通过归属断言。

| 指标 | 每 Run 新连接 p50 | 每 Run 新连接 p95 | 复用连接 p50 | 复用连接 p95 |
|---|---:|---:|---:|---:|
| connect / acquire | 1,186.18 ms | 1,346.52 ms | 0.40 ms | 0.58 ms |
| 首文本，模拟模型 | 1,404.32 ms | 1,555.30 ms | 29.82 ms | 49.33 ms |
| 轮次总耗时，含 resumed context 观测、不含 disconnect | 1,444.52 ms | 1,592.53 ms | 38.89 ms | 90.07 ms |

这是连接生命周期的受控对照，**不是 AG-UI、真实模型或浏览器 TTFT**。它没有 Postgres/Redis 的真实读写延迟，也没有四个生产 MCP server、生产 Skills、真实沙箱创建或用户并发。不可把 0.40 ms 当成线上 acquire SLA，也不可把模拟模型首文本时间宣传为产品首字。独立进程实验能证明 CLI 复用与回调切换可工作，线上收益仍须用真实 Run A/B 测量。

租约期限与 SessionStore 兼容性收尾阶段，另以每组 3 轮做真实 CLI smoke，记录在 [收尾 smoke](results/sdk-warm-final-173-20260929.json)，含当时 SDK 版本与 warm 模块 SHA-256。之后补充的取消尾部镜像修复另有专项测试覆盖。

## 验证

- 438 项相关回归通过：unit/runtime、integration/runtime、Worker Session gate、Redis gate、runtime composition、Postgres SessionStore contract。
- 随后新增“心跳尚未调度时，过期租约也不能发送缓冲输出”和“取消时只允许有执行权的 SessionStore 最后落盘、禁止工具回调”测试；warm 专项重新通过。上述共覆盖 440 个不同测试案例。
- 源码定向 Pyright：0 errors；修改文件 Ruff 与 git diff --check 通过。
- 测试覆盖两轮连接计数、真实 SDK hook 与 MCP 分发、不同用户隔离、凭据/策略变更、跨 Worker 历史 watermark、rebase、正常 workspace 快照变化、取消、迟到消息、TTL/容量、禁止重放已发送 query、Redis 失权停止、Postgres 子会话写入与删除。

## 灰度配置及边界

```text
HARNESS_SDK_WARM_ENABLED=true
HARNESS_SDK_WARM_IDLE_SECONDS=120
HARNESS_SDK_WARM_MAX_SESSIONS=4
```

以上为建议的小规模灰度值，未写入现有部署。默认值分别为 false / 300 / 16。每个 Worker 独立限制容量；此阶段没有新的数据库表、迁移或 AG-UI 协议。

173 演化栈当前单 Worker，适合首先灰度。174 当前 3 个 Worker，这版支持安全的机会式命中：会话转移后通过 transcript revision 冷恢复；**尚未增加 Worker 会话亲和路由**，因此不能保证 174 的命中率。不要把 173 单 Worker 的 100% 命中直接外推到 174。

发布需以目标当前镜像源码应用补丁，不能用此工作区完整覆盖 173 的后续 context-compaction 改动。先验证真实多轮工具/附件/Skill 路径、审批与 Artifact 归属，再对比同 Agent/模型/负载的首字 p50/p95、失败率、CLI RSS、warm 命中率，以及 `harness.sdk.connect` / `harness.sdk.reuse` span。禁用开关并重建 Worker 可恢复冷路径；进行中的任务应排空后再重启。
