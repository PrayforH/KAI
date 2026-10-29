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

| 项 | 内容 | 状态 |
| --- | --- | --- |
| 1.1 | 引用协议后端：forwardedProps.replyTo 校验→run.input.reply_to 持久化；history 每页带 replies 映射；GET messages/{id} 按 ID 定位端点 | 已实现+已测试 |
| 1.2 | 引用条 UI：用户/助手消息操作区回复按钮；输入框内顶部引用条+细线分隔；取消保留草稿 | 已实现+已测试(单测) |
| 1.3 | 发送路径：HarnessHttpAgent.setReplyTo 一次性附带；草稿刷新恢复引用；切换会话不带引用 | 已实现+已测试(单测) |
| 1.4 | 渲染：被引用消息头（点按定位/按 ID 取上下文/原消息不可用降级）；个人助理占位符「消息 KAI」 | 已实现+已测试(单测) |
| 1.5 | 专属主助理页面深化（结果优先卡片增强、配置身份独立化） | 部分（既有 /assistant 专属线程+AssistantResults 已在 79 上线；后续批次继续） |
| 1.6 | 生产构建 `next build` 通过；vitest 1379 过（4 个 nav spec 失败在基线复现，非本轮引入） | 已实现+已测试 |

已测试（阶段1，提交 072c4148）：后端 `pytest tests/unit/agui tests/integration/api/test_agui_reply_reference.py
tests/integration/api/test_agui_history_pagination.py` 104 过；typecheck 剖面与基线一致（2449/1160/234）；ruff 全过；
web `npm test` 1379 过 + `npm run build` 过。
未验证：真实浏览器点击链路与 79 部署后视觉验收（阶段5）。

## 阶段 2：已有任务托管闭环与跨端交付（8.2/8.3/8.4/8.7 P1）

| 项 | 内容 | 状态 |
| --- | --- | --- |
| 2.1 | Objective 扩展 managedThreadId + mode（autonomous/follow_only）；旧事项不变 | 已实现+已测试 |
| 2.2 | assistant_managed_tasks 表（0056，部分唯一索引：同用户同 thread 至多一个执行托管者）+ 查重返回既有托管者 | 已实现+已测试 |
| 2.3 | 托管执行在原线程续跑（execution.thread_id=原 thread）；follow_only 永不执行 | 已实现+已测试 |
| 2.4 | 互斥派发：原线程有未终结 Run 时托管执行保持 pending，下一租约周期接续（用户直聊与派发共用线程不抢跑） | 已实现+已测试 |
| 2.5 | 结构化检查点：assistant_checkpoints（执行结束同事务落库）；task_status 带检查点与托管线程 | 已实现+已测试 |
| 2.6 | 工具：assistant_resolve_target（候选解析不猜最近）/ assistant_manage_work（绑定/查重/agent 一致性）/ assistant_send_instruction（accepted/queued 分明，走 steer） | 已实现+已测试 |
| 2.7 | 结果回流主会话（主会话内嵌结果卡片）与飞书/微信引用→内部任务路由 | 部分（收件卡已带 thread_id、站内 Inbox 可溯源；主会话投影卡与渠道引用映射待后续批次，未验证） |

已测试（阶段2，提交 9f0eda93）：tests/unit 2199 过（1 个既有 cryptography-vex 失败在基线复现；
3 个同名测试文件收集冲突为仓库既有问题、基线一致）；integration storage 400 过（1 个既有 VM-host 失败在基线复现）；
ruff 全过；pyright 剖面与基线一致（2449/1160/234）。

## 阶段 3：持久专职子助理、首次配置与转交（8.9/8.10/8.11）

| 项 | 内容 | 状态 |
| --- | --- | --- |
| 3.1 | assistant_specialists（slug 查重、revision 化结构化配置、身份与配置分离）；Objective.specialistId（定时能力属于职责） | 已实现+已测试 |
| 3.2 | 配置状态机 draft/needs_input→ready（缺项集合 schedule_minutes/notify_channel；保存≠验证）；active 门禁（缺项拒绝启用） | 已实现+已测试 |
| 3.3 | 转交回执 assistant_handovers（message_id 幂等；sent→received→applied/pending_input；重放不重建） | 已实现+已测试 |
| 3.4 | delegate_to_specialist：创建/复用 + 职责登记 + 缺项不启动；assistant_specialist_status | 已实现+已测试 |
| 3.5 | API：specialists CRUD/config/status + handovers | 已实现+已测试 |
| 3.6 | 凭据专用加密输入接口与 credential_ref 隔离；子助理领域记忆 | 未实现（凭据仅设计约定"引用不入模型"，独立输入接口待建） |
| 3.7 | 子助理管理面板：/studio/assistant 新增「专职子助理」标签页（列表/配置状态/配置表单/启用暂停/转交记录/打开职责会话）；收件卡带「打开原任务」链接 | 已实现+已部署(79)+已测试(单测/构建) |

## 阶段 4：项目托管、持续运营、Computer 与预算（8.2/8.6/8.7 P2/P3）

| 项 | 内容 | 状态 |
| --- | --- | --- |
| 4.1 | ObjectiveBudget（maxRunsPerDay/maxTotalRuns）：入队事务内原子计数，达到即拒派（含次日恢复/总量永久两路径测试）；token/费用上限需计量对接，未开放 | 已实现+已测试 |
| 4.2 | 暂停 vs 立即停止语义：pause 停调度不停在途（既有），stop 经 _reconcile 取消绑定 Run（既有）；预算/截止/缺项拒派语义与 4.1 共闸门 | 已实现+已测试（既有部分） |
| 4.3 | 项目级委托（任务集合/纳管选项/新建范围/依赖无环/资源隔离/成果汇总） | 未实现 |
| 4.4 | Computer 多任务排队仲裁与跨项目隔离增强（现有：共享桌面单执行者、接管 ACK、恢复重观察已在 79 验证过） | 未实现（本轮无新增） |
| 4.5 | 事件驱动持续运营（无变化不唤起模型）、消息去重升级、成本耗时指标、故障回放 | 未实现 |

## 阶段 5：79 渐进部署与真实验收（8.8）

| 项 | 内容 | 状态 |
| --- | --- | --- |
| 5.1 | 测试/静态检查/生产构建（后端 2199 单测+400 集成、web 1379+build，ruff/pyright 对齐基线） | 已测试 |
| 5.2 | 部署 79：迁移 0055→0057（备份先行、在途 Run=0 确认）、六服务叠加镜像 `20261030-managed-b3c46010`、Viewer/VM/根盘/浏览器不动 | 已部署 |
| 5.3 | 部署后验证：healthy、容器内 grep 新代码、web chunk 指纹（消息 KAI/composer-reply-bar）、数据保全（runs 864/objectives 1/channel_files feishu 7+weixin 3 sent 与上轮一致）、匿名 401 | 已验证 |
| 5.4 | 本人渐进验收（单任务→单子助理→单项目→多项目）与真实浏览器/飞书/微信链路 | 未验证（需本人登录会话——用户级接口对 bearer 401 属身份体系设计，不绕过；清单见 results 文档） |

回滚路径：`compose.private.before-managed-b3c46010.json` + `db-backup-before-managed-b3c46010.sql.gz`
（/data/kai-assistant-managed-20261030-b3c46010/）。完整部署与验收记录：
[2026-10-30-kai-assistant-managed-79.md](../results/2026-10-30-kai-assistant-managed-79.md)。

## 时间线检查点

- 2026-10-29 深夜（本轮开始）：合同读取、起点核对、隔离工作树建立、阶段0实施中。
- 08:00 前：完成高风险迁移；08:15 收敛；08:30 停止。

## 验证记录

（每阶段实际运行过的测试、构建、部署在此追加，含命令与结果摘要。）
