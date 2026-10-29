# KAI 主助理与子助理分阶段实现（Goal 计划与状态）

- 任务标题：KAI 主助理与子助理分阶段实现（至 2026-10-30 08:30 北京时间）
- 权威材料：飞书方案 https://my.feishu.cn/docx/XV4ndItNmoONoIxQPWgcIyLbnJe 第 8 章 8.1—8.12（本地完整快照 revision 7：
  `/Users/xiaokai/.codex/visualizations/2026/10/29/assistant-managed-work/implementation-source.xml`）。
  实施合同：同目录 `zcode-implementation-brief.md`。
- 起点核对：合同声称实施起点 HEAD 2488cf3c —— 与实际一致：`/Users/xiaokai/.codex/worktrees/kai-dot-20261028/agent studio`
  在分支 `auto/kai-dot-20261028` 上 HEAD=2488cf3c41ded6d470becbba379dada74c4a99e4（`docs: record develop merge deployment and 79 acceptance`）。
- 本轮隔离工作树（本轮所有改动都在这里，不动 `/Users/xiaokai/Documents/agent studio` 的其他工作）：
  **`/Users/xiaokai/.codex/worktrees/kai-assistant-managed/agent studio`**，分支 **`auto/kai-assistant-managed-20261030`**，基于 2488cf3c。
- 模型：GLM-5.3-Flash（推理高），不擅自切换。
- 预算核对：本原生 Goal 环境报告「Token budget: none / Tokens remaining: unbounded」，不支持设置 300,000,000 token 的数值预算
  （也不能改内部数据库或购买额度）。合同 3 亿为授权上限期望；实际按真实可用额度推进有效工作，不凑用量。差别如实记录于此。
- 数据保护：保留 79 全部绑定、聊天、文件、配置、本人 VM 根盘与浏览器；不动 174。08:00（北京时间）后不启动高风险大迁移，
  08:15 收敛检查与证据，08:30 停止新工作、安全交接。

## 状态图例

每项标注：`已实现` / `已测试` / `已部署(79)` / `未验证`。只有真实验证过才升级标注。

## 阶段 0：校准与可靠性修复（8.1 静态发现 + 8.7 P0）

对应 8.7 P0 交付门槛：重复请求不重复建事项；状态查询可直接用于正确版本的控制；补充在真实适配器入档。

| 项 | 内容 | 状态 |
| --- | --- | --- |
| 0.1 | `AssistantDelegationGateway.add_note` 缺少 `await`（把协程对象当 expected_revision 传给 repository.update，真实库必然 ConflictError）→ 修复 | 已实现+已测试 |
| 0.2 | `task_status` 用 `executions[-1]` 取“最新执行”，真实 repository 按 `created_at desc` 返回（取到最旧）→ 改为取最新并固化排序契约（newest-first） | 已实现+已测试 |
| 0.3 | `task_status` 不返回 revision，而 `update_task` 要求 expected_revision → 状态输出带 revision；update 成功后回执带新 revision | 已实现+已测试 |
| 0.4 | 创建幂等：重复工具调用各自新建 Objective。新增 `idempotency_key`（delegate:{run_id}:{sha256(task...)}）落库唯一索引 + 冲突返回既有 Objective；迁移 0055 | 已实现+已测试 |
| 0.5 | 排队措辞：入队≠执行中。文案改为「已安排（排队等待后台执行）」，状态以真实 execution 状态为准 | 已实现+已测试 |
| 0.6 | 递归委派边界：composition 对所有带 agent_name 的 session 注入委派工具 → 只对个人助理协调线程（`PERSONAL_ASSISTANT_THREAD`）注入，后台 objective 执行线程不再携带委派工具 | 已实现+已测试 |
| 0.7 | 真实 Gateway＋Repository 集成测试（真实 PostgresAssistantRepository：排序、add_note await、幂等创建、revision CAS 链路） | 已实现 |

已测试（阶段0，2026-10-30 凌晨，全部在本工作树实际运行）：
- `uv run pytest tests/unit/assistant/ -q` → 62 passed。
- `HARNESS_TEST_DATABASE_URL=…:35432 uv run pytest tests/integration/storage/ -q` → 232 passed，1 failed
  （`test_computer_vm_host_postgres.py::test_reset_fences_through_the_guest_and_keeps_the_vm_alive`，
  已在未改动基线 2488cf3c 工作树复现同样失败，属既有环境问题，与本轮改动无关）。
- `uv run ruff check <改动文件>` → 全过；`scripts/check_type_baseline.py` 错误剖面与基线完全一致
  （2449 existing / 1160 new / 234 resolved，基线工作树同样数字；该脚本在基线也 exit=1，属既有漂移，非本轮引入）。
- 测试环境备注：本机 Docker 构建缓存曾把 Docker 虚拟盘占满（0 可用），已清理 ~8GB 恢复；
  Redis/MinIO 测试容器（kai-ci-redis/kai-ci-minio）重启后 infra 测试恢复。

## 阶段 1：独立主助理与引用/卡片基础（8.5 / 8.12）

- 专属主助理页面与沟通规则（不套通用助手任务过程界面）；结果优先卡片（需要你处理/正在推进/已交付）。
- 8.12 引用回复：回复按钮→输入框内顶部引用条（回复图标+摘要+取消）、细线分隔、取消保留草稿；
  `reply_to_message_id` 持久化、发送后可定位原消息；刷新/跨端/权限失效行为。
- 卡片持久数据与真实动作回执。

状态：未开始。

## 阶段 2：已有任务托管闭环与跨端交付（8.2/8.3/8.4/8.7 P1）

- 绑定原 thread（ManagedTask 关联）、只跟进/自主推进、结构化检查点、互斥派发、运行中补充（accepted/queued/failed 不混淆）。
- 结果回流主会话：VM→Artifact→鉴权预览/下载→Outbox 原生文件；补发不重跑模型；sender accepted 与用户打开分别记录。
- Web/飞书/微信目标路由与歧义澄清；引用 ID 无可靠映射时用短任务编号。

状态：未开始。

## 阶段 3：持久专职子助理、首次配置与转交（8.9/8.10/8.11）

- 一个主助理＋按需专职子助理（复用已发布能力模板/目录版本）；查重；首期禁止子助理再建下级。
- 自然语言职责→配置草案→连接/阈值/频率/时区/通知卡→验证→首次试运行→真实登记调度；缺项不启动；下次执行时间来自服务端。
- 转交回执：已发送/已接收/已应用/执行中/待补充/失败；稳定 ID+版本+幂等；凭据专用加密接口，模型仅得 credential_ref。

状态：未开始。

## 阶段 4：项目托管、持续运营、Computer 与预算（8.2/8.6/8.7 P2/P3）

- 项目任务集合、纳管选项、新建范围、依赖无环、资源隔离、成果汇总。
- 事件驱动＋定时兜底；无变化不唤起模型。权限交集、撤销立即失效。
- Computer 共享桌面单执行者、人工接管真实 ACK、恢复重新观察。
- 预算原子预留/结算；截止/额度到达不继续派发；暂停与立即停止语义分离。

状态：未开始。

## 阶段 5：79 渐进部署与真实验收（8.8 验收合同）

- 有意义范围测试、静态检查、生产构建 → 本人单任务→单子助理→单项目→多项目渐进验收。
- 使用现有 79 环境与认证；兼容迁移；按当前 compose 只更新必要服务；保留来源版本与回滚路径。
- 禁止生产清库、删卷、重建本人 VM。

状态：未开始。

## 时间线检查点

- 2026-10-29 深夜（本轮开始）：合同读取、起点核对、隔离工作树建立、阶段0实施中。
- 08:00 前：完成高风险迁移；08:15 收敛；08:30 停止。

## 验证记录

（每阶段实际运行过的测试、构建、部署在此追加，含命令与结果摘要。）
