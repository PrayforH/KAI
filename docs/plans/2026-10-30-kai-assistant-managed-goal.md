# KAI 主助理与子助理分阶段实现（Goal 计划与状态）

- 任务标题：KAI 主助理与子助理分阶段实现（至 2026-10-30 08:30 北京时间）
- 权威材料：飞书方案 https://my.feishu.cn/docx/XV4ndItNmoONoIxQPWgcIyLbnJe 第 8 章 8.1—8.12（本地完整快照 revision 7：
  `/Users/xiaokai/.codex/visualizations/2026/10/29/assistant-managed-work/implementation-source.xml`）。
  实施合同：同目录 `zcode-implementation-brief.md`。
- 起点核对：合同声称实施起点 HEAD 2488cf3c —— 与实际一致：`/Users/xiaokai/.codex/worktrees/kai-dot-20261028/agent studio`
  在分支 `auto/kai-dot-20261028` 上 HEAD=2488cf3c41ded6d470becbba379dada74c4a99e4（`docs: record develop merge deployment and 79 acceptance`）。
- 本轮隔离工作树（本轮所有改动都在这里，不动 `/Users/xiaokai/Documents/agent studio` 的其他工作）：
  **`/Users/xiaokai/.codex/worktrees/kai-assistant-managed/agent studio`**，分支 **`auto/kai-assistant-managed-20261030`**，基于 2488cf3c。
- 模型：**2026-10-30 恢复授权后改为 DeepSeek deepseek-flash（推理高）**（原 GLM-5.3-Flash 额度耗尽）；
  补充合同 `deepseek-resume-brief.md` 优先于原合同的旧模型/旧截止，用户未设新截止。
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
| 2.7 | 结果回流主会话：主助理页「后台任务进展」条（需要你处理/正在推进/已交付三类卡、打开原任务、隐藏/展开）轮询收件箱；渠道引用映射 | 主会话进展条已实现+已部署(79)+chunk 指纹验证；真实点击链路与飞书/微信引用映射未验证 |

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
| 3.6 | 凭据专用加密输入接口：`POST specialists/{id}/credentials` → McpCredentialCipher(AES-GCM, 租户/用户/引用 AAD) 密文入 `assistant_specialist_credentials`（迁移 0059），config 只写 `credential_ref:<名称>`，无读取接口；子助理领域记忆 | 凭据接口 已实现+已测试+已部署(79 r3)；领域记忆 已实现+已测试+**79 真实写入/召回/隔离已验收**（58890381 远程 MCP 令牌按 specialist:{slug} + be2d69d8 Bot 会话线程 scope；证据见 results/2026-11-02-superagent-79.md「子助理领域记忆…」节） |
| 3.7 | 子助理管理面板：/studio/assistant 新增「专职子助理」标签页（列表/配置状态/配置表单/启用暂停/转交记录/打开职责会话）；收件卡带「打开原任务」链接 | 已实现+已部署(79)+已测试(单测/构建) |

## 阶段 4：项目托管、持续运营、Computer 与预算（8.2/8.6/8.7 P2/P3）

| 项 | 内容 | 状态 |
| --- | --- | --- |
| 4.1 | ObjectiveBudget（maxRunsPerDay/maxTotalRuns）：入队事务内原子计数，达到即拒派（含次日恢复/总量永久两路径测试）；token/费用上限需计量对接，未开放 | 已实现+已测试 |
| 4.2 | 暂停 vs 立即停止语义：pause 停调度不停在途（既有），stop 经 _reconcile 取消绑定 Run（既有）；预算/截止/缺项拒派语义与 4.1 共闸门 | 已实现+已测试（既有部分） |
| 4.3 | 项目托管核心：ManagedTask.dependsOnObjectiveId（迁移 0058，绑定服务端无环校验+深度上限）；assistant_manage_work 多线程纳管（observe 角色收集到同一职责）；协调执行线程只读状态工具（无委派权，递归保持关闭）；跨项目纳管需该项目授权 | 已实现+已测试+已部署(79 r2)；新建任务范围/成果汇总/项目面板未实现 |
| 4.4 | Computer 多任务排队仲裁与跨项目隔离增强（现有：共享桌面单执行者、接管 ACK、恢复重观察已在 79 验证过） | 单执行者 + occupancy 位次/原因 + 顶栏 chip 已落地；跨项目等待文案已增强（84439439：project_id 不同即点名「跨项目」并带 holder 职责标题，缺字段安全回退）；FIFO 队头 acquire 已落地（753f6d93：桌面空闲时仅最早 pending 桌面执行可取，位置投影与放行同序，不抢占活 holder、不改 TTL/唯一索引）；两笔（84439439+753f6d93）已随 869febf5 部署 79 并容器内核对；可选优先级权重已落地（af0b4edf：budget.desktopPriority 整数缺省 0 走 payload 无迁移，队列=优先级降序+FIFO，occupancy 同序并只读透出 desktop_priority；**对活 holder 仍不抢占**）；抢占活 holder 仍未做 |
| 4.5 | 事件驱动持续运营（无变化不唤起模型）、消息去重升级、成本耗时指标、故障回放 | skipUnchanged（无变化不唤起，R5）、检查点计量（R4 tokens/cost，未计量显式说明）、失败回放（浮标失败卡深链 thread+trace+run+checkpoint）已落地；「消息去重升级」已落地（4aaedddf：important 去重改为结构化实质指纹——kind/objective 状态/run·execution·事件序号/成果名+类型+内容哈希，正文措辞不再参与；单测+渠道集成测过；未做渠道真发验收） |

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


## 第二轮（DeepSeek 恢复后）实际完成情况

| 项 | 内容 | 状态 |
| --- | --- | --- |
| R1 | 中文职责名查重：slug 附加名称短哈希，同名复用、异名各建（真实中文名集成测试） | 已实现+已测试 |
| R2 | 成果汇总：managed_overview/render + coordinator 提示注入（只读、读不到不猜） | 已实现+已测试+已部署 |
| R3 | 子助理领域记忆：MemoryBankGateway scope + specialist_memory_scope（specialist:{slug}） | 已实现+已测试+**79 真实写入/召回/隔离已验收**（写=propose_memory 落 specialist:{slug}；读=scoped 投影零工具召回；跨 Bot 检索无命中+匿名 401；两缺口 58890381/be2d69d8 已修且 79 live 20261103-b68e88eb 原生包含） |
| R4 | 计量：检查点记 tokens/cost/unknown_usage（从配额账本读真实用量）；预算 maxTokens/maxCostUsd；未计量时拒派并说明 | 已实现+已测试+已部署（79 实测拒绝文案） |
| R5 | 事件驱动：skipUnchanged + 进展指纹，定时无变化不唤起模型 | 已实现+已测试 |
| R6 | Computer 单执行者：assistant_desktop_turns（部分唯一索引、幂等、TTL 回收、释放交接） | 已实现+已测试 |
| R7 | 新建任务授权范围：parentObjectiveId + maxNewTasks + 观察者"仅新建"工具 | 已实现+已测试+已部署 |
| R8 | 渠道引用：飞书 parent_id/root_id、微信 refer → 内部 user-<run_id>；解析不到不猜 | 已实现+已测试(单测)；真实渠道收发待本人操作 |
| R9 | 项目托管面板 + 预算/授权表单 + 子助理试运行/挂职责 + 结果进展条 | 已实现+已部署+真实浏览器验收 |
| R10 | 79 部署：迁移 0055→0063，r4 镜像（api/worker/control/feishu/weixin/web），备份先行、在途 Run=0 | 已部署 |
| R11 | 真实页面验收：引用条/取消保草稿/刷新与跨浏览器恢复/点击定位、托管面板、子助理配置→试运行→启用、预算落库与门禁、成果下载字节一致 | 已验证 |
| R12 | 飞书/微信入站与引用真实收发 | **待本人操作**（无客户端输入能力） |

## 第三轮（对话形态改造，用户反馈驱动）

| 项 | 内容 | 状态 |
| --- | --- | --- |
| C1 | 布局修复：高度链 flex/min-height:0，对话区内部滚动，输入框恒在视口内 | 已实现+已测试+已部署（实测 composer 790–890/视口 900） |
| C2 | 进展条改悬浮浮标（33px 折叠、可展开、可收起、不占文档流） | 已实现+已测试(3 个组件测试)+已部署 |
| C3 | 气泡对话形态：助手 `.assistant-answer` 圆角气泡、用户右侧强调气泡、状态行居中弱化；仅作用 `data-surface=assistant` | 已实现+已部署 |
| C4 | 左栏「机器人」：主助理置顶、专职子助理带头像/副标题、当前高亮、展开收起 | 已实现+已部署 |
| C5 | 子助理独立会话：`GET /specialists/{id}` 返回职责线程，`/assistant?bot=` 直接进该对话，输入框「给 X 发消息」 | 已实现+已测试+已部署（实测收发 BOT_OK） |
| C6 | 客户端独有的右侧面板（详情/资料库/电脑） | 未实现（按用户说明属客户端特性） |
