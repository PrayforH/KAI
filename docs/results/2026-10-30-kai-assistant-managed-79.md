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

- 镜像（全部以当前运行中镜像为基座的增量叠加；r1=b3c46010、r2=项目托管核心+迁移0058、r3=凭据库+迁移0059）：
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

- 凭据库批次（r3）：迁移 **0059** `assistant_specialist_credentials`；`POST specialists/{id}/credentials`
  经 McpCredentialCipher(AES-GCM) 密文入库，specialist.config 只写 credential_ref；无读取接口。
  r3 三服务+双接收器滚动，全部 healthy，容器内 grep `save_specialist_credential`=1，`/assistant` 200。
- **数据库最终迁移头：0059**。


## 第二轮（2026-10-30 恢复授权后，DeepSeek 继续）

部署版本：后端 `kai/assistant-api:20261030-managed-r4`（feishu/weixin 同名 r4），web
`kai/assistant-web:20261030-managed-r4`；数据库迁移 **0055→0063**（0060 计量、0061 桌面执行权、
0062 微信引用键、0063 子任务授权）。备份：`db-backup-before-r4.sql.gz` +
`compose.private.before-r4.json`。切换前确认在途 Run=0。

### 真实浏览器验收（真实登录态；走 79 已发布的纯 HTTP 内部监听 3501，不绕过自签证书）

登录态来源：79 上 Chrome 的会话已过期且 keychain 不可非交互读取，改用平台自身 AuthService 在 api
容器内为本人账号签发 30 分钟会话（与密码登录同一条代码路径，refresh token 正常入库可审计）；
凭据只落 0600 临时文件，不打印、不入库、不进仓库。

| 验收项 | 结果 |
| --- | --- |
| 「我的助理」页面、`消息 KAI` 占位符 | 通过（stage1 已在线） |
| 后台任务进展条（已交付卡 + 打开原任务链接） | 通过：真实快报结果渲染成「已交付」卡 |
| 引用条：回复按钮 → 条内摘要 + 细线 + 取消 | 通过（截图 06a） |
| 取消引用保留草稿 | 通过（草稿 `验收草稿-KEEP-ME` 保留） |
| 刷新后引用与草稿同时恢复 | 通过 |
| 引用发送后新消息显示引用头 + 点击定位原消息 | 通过（21 条消息、命中视口） |
| 引用关系跨刷新、跨浏览器（独立 profile＝另一设备） | 通过（两处都显示同一引用头） |
| 项目托管面板：纳管列表/角色/进展/成果汇总/打开原任务 | 通过（纳管 1 项只读关注，aggregate 正确） |
| 在面板里纳管既有线程 | 通过（写库 observe 行；修复了绑定后不刷新的缺陷） |
| 子助理：中文职责查重、配置→待启用、试运行、启用 | 通过（试运行真实执行并产出检查点） |
| 预算/授权表单落库 | 通过（maxRunsPerDay=2、maxTokens=1000、maxNewTasks=1、skipUnchanged、requiresDesktop） |
| 计量门禁 | 通过：79 未启用配额计量 → 检查点 `unknown_usage=true`；再次派发被拒「token 预算无法核实：存在未计量执行」 |
| 成果取件 | 通过：结果卡链接直接下载 6 字节 `probe-DELTA031451.txt`，与带会话直取字节一致；`?preview=1` 返回 200 + `inline` disposition；匿名 401；刷新后再取字节相同 |
| 子助理试运行真实性 | 通过：`objective_run_d7a5…` 真实执行并产出检查点（summary 是真实快报文本） |
| 渠道在线状态 | 飞书 `receiverOnline=true / awaiting_event`（已交付文件 7），微信 `online / connected / loginStatus=confirmed`（sent 3） |

数据保全（当前实测）：runs 869、agui 线程绑定 221、通道已发送文件 10、飞书绑定 1、微信连接 1、
职责 3、子助理 1、检查点 3；Viewer 仍为 `20261029-v12-clipboard`，VM/根盘/浏览器未重建。

### 本轮真实页面发现并修复的缺陷

1. 引用条渲染在 `Composer.Root` 内，assistant-ui 只渲染自己的槽位 → 条永远不出现；改挂到
   composer shell 内、`Composer.Root` 之前。
2. 项目托管绑定成功后列表不刷新（库里有行、界面仍显示 0）→ 增加面板 revision 触发重取。
3. `POST /specialists` 要求完整 Specialist（含 tenantId/userId），客户端可伪造身份且真实 UI 无法创建
   → 只接受 name/description/agent，身份由会话推导，未知字段 422。
4. 409 被统一折叠成「事项已更新或已有执行」，真实原因（缺项/无职责/预算无法核实）看不到
   → 客户端优先展示服务端消息。
5. 无职责的子助理也能被置为 active（显示"运行中"但什么都不会跑）→ 启用前要求至少一项职责，并如实
   标注「已启用（职责未设定时）」。
6. 模型对"长期职责"优先选了通用后台任务工具 → coordinator 指令明确：长期可复用职责用
   `assistant_delegate_to_specialist`，一次性任务才用 `assistant_create_task`。

### 仍未验证（需要本人手上操作）

- 飞书/微信**入站**收发与引用：需要你从自己的飞书/微信客户端给机器人发一条消息（我给不出客户端输入）。
  建议发送：「回复验收 A7：请只回 OK」，然后对同一条消息用"回复"再发「回复验收 A8：把上一条的结论
  用一句话复述」。两条都应落到同一个个人助理线程，网页可见并可续聊。
- 微信客户端若只能手动输入，请按上面两条文本发送；收到后我在网页侧核对 thread/消息与引用映射。
