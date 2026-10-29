# KAI 主助理与子助理分阶段实现部署 79（2026-10-30）

部署源码提交 `6a1f16a7`（分支 `auto/kai-assistant-managed-20261030`，基于 2488cf3c；镜像标签 `20261030-managed-b3c46010-r2`，web 为 `20261030-managed-b3c46010`），工作树
`/Users/xiaokai/.codex/worktrees/kai-assistant-managed/agent studio`。部署目录
`/data/kai-assistant-managed-20261030-b3c46010`（含 compose 备份与 DB 备份）。

## 变更范围（五个提交）

| 提交 | 内容 |
| --- | --- |
| 51486189 | 阶段0：delegation 可靠性——add_note await、最新执行排序、revision 返回、创建幂等（迁移 0055）、排队措辞、递归委派门控 |
| 072c4148 | 阶段1：8.12 引用回复全栈——replyTo 协议（run.input.reply_to 持久化）、history replies 映射、按 ID 定位端点、引用条 UI、消息 KAI 占位符 |
| 9f0eda93 | 阶段2：原任务托管闭环——Objective.mode/managedThreadId、assistant_managed_tasks（同 thread 单执行托管者）、托管执行在原线程续跑、直聊互斥、结构化检查点（迁移 0056）、resolve/manage/instruction 工具 |
| ab2156fa | 阶段3：专职子助理——assistant_specialists + assistant_handovers（迁移 0057）、配置状态机（缺项不启用）、转交回执 sent/received/pending_input、delegate/status 工具与 API |
| b3c46010 | 阶段4：预算派发闸门（maxRunsPerDay/maxTotalRuns，入队事务内原子计数），token/费用上限需计量对接、未开放 |

## 部署过程

- 镜像（全部以当前运行中镜像为基座的增量叠加，revision 标签 `20261030-managed-b3c46010`）：
  api/worker/computer-control=`kai/assistant-api`、web=`kai/assistant-web`、feishu=`kai/feishu-receiver`、
  weixin=`kai/weixin-receiver`。Viewer 保持 `20261029-v12-clipboard`，未重建 VM/根盘/浏览器。
- 迁移：新 api 镜像 `alembic upgrade head`，0054→0055→0056→0057，库 `harness_174_20260908`
  （79 API 容器 `HARNESS_DATABASE_URL` 指向库；同实例的 `harness` 库是另一套数据，未触碰）。
- 迁移前备份：`db-backup-before-managed-b3c46010.sql.gz`（816MB 库，dump 152MB）+
  `compose.private.before-managed-b3c46010.json`（0600）。切换前确认在途 Run=0（仅 1 条 succeeded）。
- 切换流程沿用上一轮 roll.sh：先停 feishu/weixin 接收器 → 活跃运行检查 → 改 compose 镜像 →
  config 校验 → 六服务 up -d。
- 踩坑两处（均有记忆先例）：① macOS tar 必须配 `COPYFILE_DISABLE=1`/删 `._*`，AppleDouble 文件
  被 alembic 当 Python 读（"null bytes"）——已在 79 上删除 `._*` 后重建镜像；② web 多阶段构建的
  build context 必须含 `web/harness-console`，feishu/weixin 必须用各自运行镜像做基座（首建误用 api
  基座，镜像 ID 相同暴露问题，已重建）。

## 已验证

- 六容器全部 healthy（api/worker/computer-control/web）或 Up（feishu/weixin），https 入口 200、
  `/healthz` 200、`/assistant` 页 200。
- 容器内 grep 新代码：三个后端镜像含 `0056_managed_tasks_checkpoints.py` 且
  `delegation.py` 含 `delegate_to_specialist`；web chunk 同时含 `消息 KAI` 与 `composer-reply-bar`。
- 数据保全：runs 864 / objectives 1（completed，revision 5，与切换前一致）/ inbox 1 /
  channel_files 10（feishu sent 7、weixin sent 3，与上一轮验收记录逐项一致）/ thread bindings 219。
- 匿名访问 `/v1/studio/assistant/specialists` → 401（身份门禁生效）。
- 测试：后端 tests/unit 2199 过（1 个既有 cryptography 失败在基线 2488cf3c 复现）；
  integration storage 400 过（1 个既有 VM-host 失败在基线复现）；ruff 全过；pyright 剖面与基线一致；
  web vitest 1379 过（4 个 nav spec 失败在基线复现）；`next build` 通过。

## 硬阻塞与待用户操作（未能完成的验收项）

- 用户级 HTTP 接口（objectives/inbox/history 读与真实托管操作）需要本人登录会话；本轮使用的
  bearer 服务 token 不适用于用户域接口（401，符合身份体系设计），无法也不应绕过。
  因此以下验收需要本人在浏览器登录 https://172.20.105.79:3543/assistant 后进行：
  1. 单任务渐进验收：对既有任务发起接管（follow_only 与 autonomous 各一），观察托管卡、
     检查点与原线程续跑；
  2. 引用回复：回复某条消息→引用条出现→取消保留草稿→发送后定位原消息；
  3. 子助理：`帮我长期关注某账号花费`→转交回执 pending_input→补齐频率/通知→ready→启用；
  4. 飞书/微信回复引用的跨端定位（引用协议已铺后端，真实渠道映射未在本轮验证）。
- 阶段4其余项（项目托管的任务集合/依赖无环/Computer 并发仲裁与预算 token 结算）未实施，见计划文档。

回滚：恢复 `compose.private.before-managed-b3c46010.json` 后逐服务切回
`20261029-develop-8f2c3a8f` 镜像；保留增量表与新数据，不降级数据库、不删数据。

本报告不含登录口令、token 值或私有 compose 内容。

## 追加批次（同日 02:30 前后）

- 阶段3 UI 补齐：`/studio/assistant` 新增「专职子助理」标签页（列表、配置状态徽标、配置表单
  `specialists/{id}/config`、启用/暂停走 `status` 命令、转交记录、打开职责会话），收件卡带
  「打开原任务」直达链接（inbox 现携带 threadId）。web 镜像二次构建并只滚动 web 服务，
  运行容器 chunk 含「专职子助理」文案，`/assistant` 页 200。
- 该批次的真机点击链路同样属于 5.4（需本人登录会话）。

- 阶段2.7 主会话结果投影补齐：`/assistant` 页新增「后台任务进展」条
  （`AssistantProgressStrip`：按 8.5 只展示 需要你处理/正在推进/已交付 三类卡片，
  15 秒轮询收件箱、打开原任务、可隐藏），web 镜像三次构建并滚动，运行容器 chunk
  含 `assistant-progress-strip`/`后台任务进展`，`/assistant` 200。

- 阶段4.3 项目托管核心（r2 滚动）：迁移 0058（managed task 依赖边）、绑定服务端无环校验
  （成环/超深拒绝）、`assistant_manage_work` 多线程纳管（observe 收集）、协调执行线程只读
  状态工具（observer 门控，委派权仍关闭）。r2 镜像三服务 + 双接收器滚动，容器内 grep
  `_ensure_acyclic`=2，全部 healthy，`/assistant` 200。
- 数据库最终迁移头：**0058**。备份链：`db-backup-before-managed-b3c46010.sql.gz`（0054 时点）。
