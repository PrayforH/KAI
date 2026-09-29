# 提交请求 → 持久接受 → 可靠入队 → 开始执行：第一轮实施记录

日期：2026-09-29
基准：`origin/develop` = `5fe3772fd29fd08b4855093169c904a586b9df5a`
分支：`auto/reliable-run-submit`（独立 worktree，主工作区未提交改动未触碰）
范围：只做「接受即持久化执行意图」的可恢复闭环；不部署、不改线上配置、不合并。

---

## 一、现状核实（改动前，全部在代码中确认）

| 编号 | 结论 | 证据 |
| --- | --- | --- |
| 1 | Run 创建、`run.queued` 事件、Redis 入队分三步各自提交 | `src/harness/application/runs.py` 原 `_create_locked`：`runs.add`（`repositories.py` 内独立 commit）→ `events.append`（独立 commit）→ `queue.enqueue`（Redis）。二者提交后、入队前退出即留下无执行消息的 Run |
| 2 | 幂等重试命中直接返回，不补投 | 原 `runs.py:200-207`，`find_by_idempotency_key` 命中后直接 `return RunCreation(...)`，丢失的投递永远无法恢复 |
| 3 | Outbox 未接入 | `OutboxRow`（`storage/models.py`）与 `new_outbox_record`（`storage/outbox.py`）全仓只有定义，无任何生产调用点 |
| 4 | 历史迁移引用当前 ORM | `migrations/versions/0001_initial.py` 调 `Base.metadata.create_all()`；`0006` 已用 `has_table` 守卫处理由此产生的全新库重复建表 |

补充确认：`uq_run_idempotency` 与 `0001` 同批引入（`46f588b3`），既有库均有该唯一约束，因此并发同键可由数据库裁决。`worker/orchestrator.py` 的既有幂等与执行权机制（终态直接返回、`CANCELLING` 收口、`WAITING_APPROVAL` 不重复启动、`RUNNING/PROVISIONING` 走 `_reclaim` 抬升 fencing、`_move` 的 CAS）足以承接重复投递，本轮直接复用。

## 二、改动范围与方案

**唯一权威记录**：新增 `run_execution_commands` 表作为「该 Run 必须交付给队列」的唯一 pending 记录（每个 Run 一行，`uq_run_execution_command`）。通用 `outbox` 未接入且本轮不动它——它的 schema 无法在不改造通用语义的前提下表达租约/退避，因此不作为本闭环的权威，避免出现两套无约束 pending。

**Unit of Work**：新增 `RunAcceptanceUnitOfWork` 端口，`PostgresRunAcceptance.accept(run, event, command)` 在**一个**数据库事务内写 `runs` + `run_events` + `run_execution_commands`，不是把多个自行 commit 的仓储方法包一层。`ensure_command` 为幂等补齐（供同键重试恢复丢失的意图）。

**Dispatcher**：`ExecutionCommandDispatcher` 用 `SELECT ... FOR UPDATE SKIP LOCKED` 领取，`lease_owner`/`lease_expires_at` 支持多实例与过期接管，`available_at` 承载指数退避；**领取事务提交后**才调 Redis，发布成功再回写 `dispatched`。

**投递语义**：至少一次投递 + 幂等处理。重复消息由既有 Worker 幂等/状态校验/fencing 吸收；外部模型与工具副作用不与队列消息事务化，不宣称天然 exactly-once。

### 改动文件

生产代码：
- 新增 `src/harness/worker/dispatcher.py`（Dispatcher + `running_dispatcher` 生命周期 + `safe_error` 凭据擦除）
- 新增 `src/harness/storage/execution_commands.py`（`PostgresRunAcceptance`、`PostgresRunExecutionCommandRepository`）
- 新增 `migrations/versions/0038_run_execution_commands.py`（稳定内联定义 + `has_table` 守卫）
- `src/harness/core/ports.py`（`RunExecutionCommand`、`ExecutionCommandStatus`、`execution_command_id`、`RunExecutionCommandBacklog`、两个端口）
- `src/harness/storage/models.py`（`RunExecutionCommandRow`）
- `src/harness/storage/repositories.py`（抽出 `build_run_row`/`build_event_row`，供接受事务与既有两个仓储共用同一映射）
- `src/harness/application/runs.py`（单事务接受、失败补偿、缺失意图恢复、同键不同输入的观测、内联子 Run 不生成意图）
- `src/harness/application/events.py`（`new_event`/`append_event`/`notify`）
- `src/harness/adapters/memory.py`（`InMemoryRunExecutionCommandRepository`、`InMemoryRunAcceptance`）
- `src/harness/config.py`（`worker_dispatch_*` 设置，`worker_dispatch_enabled` 默认开启）
- `src/harness/composition.py`、`src/harness/api/dependencies.py`（生产与内存装配）
- `src/harness/worker/main.py`（`serve()` 用 `running_dispatcher` 包住消费窗口）
- `src/harness/evals/controller.py`、`src/harness/studio/api.py`（本地 auto-execute 补上「投递再消费」这一跳）
- `src/harness/reliability/metrics.py`（backlog/age/失败/恢复/同键复用指标）

测试：
- 新增 `tests/unit/application/test_run_acceptance.py`（11）
- 新增 `tests/unit/worker/test_dispatcher.py`（14）
- 新增 `tests/unit/test_migration_0038.py`（4）
- 新增 `tests/integration/storage/test_run_dispatch_postgres.py`（12，真实 PostgreSQL + 真实 Redis）
- 新增 `tests/integration/storage/test_migration_0038_postgres.py`（3，真实 Alembic 回放）
- `tests/support/__init__.py`（`deliver_pending_tasks`）
- 原有 4 个文件补上「投递再消费」：`tests/unit/quality/…`、`tests/unit/evals/…`、`tests/unit/evolution/…`、`tests/integration/api/test_agent_studio_api.py`

## 三、创建 → 投递 → 重试 → 消费的完整流程

1. **接受（API 进程）**：`RunService.create_with_result` 在配额准入后构造 `Run`、`run.queued` 事件与 `command_id = dispatch:<run_id>` 的执行命令，交给 `RunAcceptanceUnitOfWork.accept`。一个事务提交三者，**提交后即返回已有协议的 202/Run**，Redis 不参与接受判定。
2. **重复判定**：同键并发由 `uq_run_idempotency` 裁决；输的一方收到 `ConflictError` → 重新读取既有 Run → `ensure_command` 幂等补写 → 返回同一个 Run，不产生第二个 Run/命令。
3. **投递（Worker 进程）**：`ExecutionCommandDispatcher.run_once` 领取一批到期命令（提交并持有租约）→ 逐条 `queue.enqueue` → 成功则 `mark_dispatched`（按 `lease_owner` 限定）→ 失败则 `reschedule`（退避 + `failures+1` + 擦除后的诊断）。
4. **崩溃恢复**：领取后退出 ⇒ 租约到期，另一实例接管（`attempts` 递增）；发布成功但未标记 ⇒ 同样重投一次，Redis 的 pending 去重与 Worker 幂等保证不产生重复有效执行。
5. **消费（Worker 进程）**：`worker_loop` → `RunOrchestrator.execute`。既有机制决定安全性：终态直接返回；`WAITING_APPROVAL` 不重复启动；`RUNNING/PROVISIONING` 走 `_reclaim` 抬升 fencing 令旧持有者让位。

## 四、命令与结果（本机真实 PostgreSQL 5432 + Redis 6379）

```
uv run python -m pytest tests/unit tests/contract tests/contracts tests/integration -q
  → 1978 passed, 8 skipped（含已安装 deepagents extra 后原本因缺依赖失败的 4 项）

uv run pyright                      → 1500 errors（与基准完全一致，未新增）
uv run ruff check src tests         → 仅 3 条基准既有错误（automations/projects 测试）
uv run python scripts/e2e_fake_runtime.py
  → status=succeeded, agui_events=52, approval_id 非空（审批往返正常）
uv run python scripts/check_agent_packages.py     → 全 READY
uv run python scripts/verify_agent_determinism.py → 全 DETERMINISTIC
uv run python scripts/final_readiness.py          → migrationHead=0038
```

十一条验收对应的用例与结果：

| # | 场景 | 用例 | 结果 |
| --- | --- | --- | --- |
| 1 | 正常提交后 Run 被 Worker 执行 | `test_a_submitted_run_reaches_a_worker_and_finishes`（真 PG+Redis，`worker_loop` 消费） | 通过，`run.succeeded`，runtime 执行 1 次，队列清空 |
| 2 | 事务失败不留部分 Run/Event/Command | `test_a_conflicting_event_leaves_no_partial_run_obligation_or_event`（真库制造事件序号冲突） | 通过，Run/命令均 0 行；冲突消除后同键仍可接受 |
| 3 | 提交后、入队前退出，重启可投递 | `test_a_run_accepted_before_the_crash_is_delivered_by_a_restarted_dispatcher` | 通过，新 owner 领取并投递 |
| 4 | Redis 不可用仍有持久接受，恢复后继续 | `test_an_unreachable_redis_still_accepts_and_recovers` | 通过，退避 2s、`failures=1`、诊断无凭据；恢复后投递成功 |
| 5 | 入队成功但标记失败，重投不产生重复有效执行 | `test_a_publish_that_cannot_be_recorded_is_redelivered_without_double_work` | 通过，租约过期重投，Redis ready 恰好 1 条 |
| 6 | 两 Dispatcher 领取与过期接管 | `test_two_dispatchers_claim_disjoint_work_and_take_over_expired_leases` | 通过，6 条不重不漏；过期租约被接管，`attempts=2` |
| 7 | 同键并发只产生一个 Run 与一个意图 | `test_concurrent_same_idempotency_key_yields_one_run_and_one_obligation`（真库 6 并发） | 通过，1 个 Run、1 条命令、1 次 `created` |
| 8 | 已有 Run 重试返回稳定结果，未投递意图可恢复 | `test_a_retry_returns_a_stable_run_and_recovers_a_missing_obligation` | 通过 |
| 9 | `dispatch_to_queue=False` 不被后台 Dispatcher 入队 | `test_an_inline_child_run_never_becomes_another_workers_task` | 通过，Run/事件各 1 行、命令 0 行、Redis 0 条 |
| 10 | 终态或已被有效执行者接管的 Run 不重复执行 | `test_a_late_duplicate_task_does_not_reexecute_a_terminal_run`、`test_dispatching_records_delivery_without_executing_or_owning_the_run` | 通过；接管语义由既有 `test_recovered_provisioning_run_is_reclaimed_and_completed` 钉住 |
| 11 | 取消/审批/Session gate/SDK 复用回归 | `test_worker_session_gate`、`test_redis_session_gate`、`test_approval_flow`、`test_approval_ownership`、`test_run_api`、`test_sse`、`test_warm_sdk`、`test_run_service`、`test_orchestrator` | 110 passed |

## 五、迁移、发布顺序与回滚限制

- 迁移 `0038` 为**新增表**，无数据改写；`upgrade` 带 `has_table` 守卫，因此「全新库（`0001` 的 `create_all` 已建表）」与「既有库从 `0037` 升级」两条路径都被覆盖并有真实回放测试：两条路径反射出的列/类型/可空/索引/唯一约束/主键**完全一致**。
- 迁移定义内联、不 import 当前 ORM（`tests/unit/test_migration_0038.py` 用 AST 断言），历史语义不随模型漂移。
- 发布顺序：**先迁移 → 再 API → 再 Worker**。只上 API 而不上 Worker 时，Run 会被持久接受但无人投递（backlog 增长，可见于指标）；只上 Worker 而不上 API 时，新表为空、旧路径照常。
- 回滚限制：`downgrade` 只删 `run_execution_commands`，`runs`/`run_events` 不动；回滚到旧代码后，所有已接受但未投递的 Run 会回到「无执行消息」状态，需要人工重投或改回幂等键重试。**因此回滚前应先确认 backlog 中 `pending` 为 0。**
- `worker_dispatch_enabled=false` 可停投递但不停接受；这不是回滚手段，只用于止血排障。

## 六、已经实现

1. 接受请求的三类写入（Run、`run.queued`、执行命令）共享同一数据库事务，提交即返回接受结果，Redis 不是接受前提。
2. 执行意图落在唯一权威表 `run_execution_commands`，字段含稳定 `command_id`、tenant/run/session 关联、状态、`available_at`、`attempts`/`failures`、`lease_owner`/`lease_expires_at`、`dispatched_at`、擦除后的 `last_error`。**该记录刻意不保存 prompt 或任何请求体**，因此它本身不可能成为敏感输入的泄漏面；日志只输出 tenant/run/command/attempts 与经 `safe_error` 擦除（URL userinfo、Authorization）并截断到 200 字符的异常描述。
3. Dispatcher 多实例安全领取（SKIP LOCKED）、领取者退出回收（租约过期）、发布失败指数退避（上限可配）、发布成功未标记允许重投（至少一次）。
4. 不在持锁事务内调用 Redis/模型/沙箱：领取事务提交后才发布。
5. 请求幂等保持：同键并发只产生一个 Run 与一个意图（真库验证）；同键不同输入**保持既有兼容行为**（返回既有 Run、不产生第二个意图），新增指标 `harness_idempotency_key_reuse_total` 与 warning 记录以便运营发现，不改契约。
6. `dispatch_to_queue=False` 内联子 Run 不生成命令，后台 Dispatcher 无法消费它。
7. 配额：接受失败时补偿释放该 run_id 的预留（新增用例覆盖失败与冲突两条路径）；进程中途退出由既有 TTL + `quota-reservation` reaper 兜底，未引入永久泄漏，未重写配额系统。
8. 内存模式、测试模式、生产装配均可用；生产装配有守卫测试断言接入了 PG 的接受事务与 Dispatcher。Dispatcher 提供 `start`/`stop`/`health`，由 Worker 入口的 `running_dispatcher` 管理生命周期，指标暴露 backlog/最老 pending age/投递失败/恢复计数，日志只含 tenant/run/command/attempts 与擦除后的异常类名+截断消息。

## 七、尚未验证 / 本轮未做

**尚未验证（不要当成已通过）**

1. **未在真实 173/174 环境部署或跑过真实 Run**。所有结论来自本机真实 PostgreSQL + Redis 的集成测试；未验证线上 Redis 代理、镜像、compose 差异下的行为。
2. **未做存量数据的恢复演练**。既有线上 Run 若无命令行，需要靠重试请求触发 `ensure_command`；没有做「扫描全部 queued 且无意图的 Run 并补投」的批处理——本轮刻意不引入该 sweep 以避免上线时对在跑 Run 的惊群重投。存量 stranded Run 的清单与补投属下一轮。
3. **未验证多 Worker 真实并发下的端到端时延**（如 backlog age 与首字节延迟的关系），也未做压测。
4. **未验证取消/审批与接受事务的交叉竞态**（例如取消请求与接受同刻到达）；本轮只做了既有回归不破。
5. `dispatch_to_queue=False` 的真实 Builder 内联路径只由既有单测（`dispatch_to_queue is False`）覆盖，未跑真实 SDK Run。
6. **未验证回滚后的人工重投流程**（rollback 会让 pending Run 失去投递者），只写下限制。
7. 未验证 `harness_dispatch_commands` 等新指标在线上 Prometheus/看板侧的可见性。

**本轮明确未做**（严格按范围）5 个 schema 物理迁移、Attempt/Turn/ExecutionPlan、Run 状态机与 RunOrchestrator 重写、审批/取消/调度的全面事务化、SDK 连接池与上下文压缩、沙箱生命周期重构、WeKnora 改造、Temporal/Kafka/新库、前端调整。

## 八、剩余风险与下一轮建议

1. **存量 stranded Run**：上线后需要一次有界、可观测的补投（或运维 SQL 建命令行），否则老 Run 只能靠重试请求恢复。建议下一轮做成显式开关 + 限量 + 指标，而不是默认开启。
2. **同键不同输入**：目前只观测不改契约。建议下一轮明确 409 语义并评估客户端兼容，避免调用方以为新 prompt 在跑。
3. **`outbox` 死代码**：`OutboxRow`/`new_outbox_record` 仍无调用点。要么下一轮让事件扇出复用它，要么删除，避免后来者误以为事件发布已有 outbox 保证。
4. **`RunService` 的 legacy 无 UoW 分支**：为进程内测试保留，误装配会让闭环退回到原始缺陷（已加一次 warning）。建议下一轮把它收敛为「必须传入 UoW」，或让端口成为构造必填项。
5. **命令表无上限**：`dispatched` 行会一直累积。建议下一轮加按时间的归档/清理（如保留 N 天），并纳入 reaper。
6. **发布与标记之间的窗口**：Redis 的 pending 去重在任务已 ack 后失效，因此同一条命令在长时间失败重试下可能被投递多次。当前由 Worker 幂等吸收，但值得在下一轮把「投递计数 vs 实际执行次数」做成对账指标。

### 顺带发现、本轮未改（按范围只记清单）

1. `RunService._creation_locks` 每个 `(tenant, session)` 存一把 `asyncio.Lock` 且从不回收，长期运行会随会话数单调增长。与会话量挂钩，不属本轮闭环，但应作为内存泄漏处理。
2. `EventService.append` 在事务提交后调用 `self._bus.publish(event)` **且不捕获异常**：Redis 失败会把异常抛回已经成功提交的调用方。本轮新增的 `notify()` 是兜底版本，只用在接受路径上，避免改变既有调用方的语义；`append` 是否也应收敛为 best-effort 需要单独评估（可能有调用方依赖这个失败信号）。
3. `outbox` 表**不在任何 Alembic 修订里**：既有库只有在 `0001` 的 legacy `create_all` 执行时 `OutboxRow` 已在模型中，该表才存在。将来若要真正接入 Outbox，必须补一个带 `has_table` 守卫的修订，不能假设表已在。
4. `RunRepository` 端口没有「按状态列出缺少执行命令的 Run」这类查询，因此本轮的存量补投只能靠重试请求触发。下一轮做 sweep 时需要新增一个跨表查询端口，而不是在服务层拼两个仓储。

