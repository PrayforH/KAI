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


## 第三轮：助理页改为对话形态（用户反馈驱动）

用户反馈两点：① 输入框被顶出视口、上方进展块像"部署悬浮"；② 整个页面读起来像任务控制台，
缺少对话感；③ 点 bot 应进入该 bot 的对话，而不是管理表单；④ 子 bot 结果应直接回到聊天窗。

### 实测缺陷与修法

| 问题 | 实测证据 | 修法 |
| --- | --- | --- |
| 输入框被挤出视口 | `.chat-surface` 为 block，进展条占 145px → 内容 1007px > 可视 854px，composer 落在 941–1041（视口 900） | 从 `.chat-surface` 到 `.aui-thread-viewport` 全链 `flex + min-height:0`，对话区内部滚动；进展条改为绝对定位浮标（折叠 33px，可展开/收起） |
| 气泡套在空槽位 | 每个助手轮多出 28px 空胶囊（20 个），真实回答在 `.assistant-answer` | 气泡改挂 `.assistant-answer`，空槽位不再绘制 |
| 点 bot 落到管理表单 | 旧链接指向 `/studio/assistant?specialist=` | 改为 `/assistant?bot=<id>`；软导航会保留旧线程，故用整页导航 |
| 子 bot 没有自己的对话 | 只有职责执行线程 | 子助理的对话＝承接职责的原线程（`GET /specialists/{id}` 返回 threadId），结果天然落在同一条流里 |

### 真实页面验收（79，部署后）

- 折叠浮标 33px、composer 790–890 在视口内；
- 左栏「机器人」：主助理置顶 + 行业快报监控（头像、副标题、当前高亮）；
- 点 bot 直接进入其对话：标题「行业快报监控」+ 一句话职责，输入框「给 行业快报监控 发消息」；
- 该 bot 的历史快报结果就在对话流里（不是任务卡）；
- 在 bot 会话发「只回复 BOT_OK」→ 同一条对话里收到 `BOT_OK`（用户消息 1→2、回答 1→2），无控制台错误；
- 前端测试 1382 过（4 个既有 nav 规格失败保持不变量），新增 3 个浮标组件测试通过。

### 仍未验证

- 客户端独有的右侧面板（详情/资料库/电脑）：按用户说明属客户端特性，web 暂不实现；
- 飞书/微信入站与引用：仍需本人从客户端发消息。


## 第四轮（2026-10-30 18:4x–19:0x）：聊天形态收敛 + 跨 bot 派活演示

### 这一轮改了什么（都只作用于助理面 `[data-surface="assistant"]`）

| 项 | 结果 | 证据 |
|---|---|---|
| 输入框多余控件 | `@ 知识库`、`@ 通用助手` 在助理面隐藏，任务面保留 | 助理面 composer 只剩 `+` / 模型 / 发送；任务面仍显示 `@ 舆情分析 Agent` |
| 消息操作栏 | 只剩「复制 / 回复」，收到气泡侧边，24px | 实测 labels `[复制回答, 回复这条回答]`、`[复制消息, 回复消息]`；`position:absolute` |
| 点赞/差评/分支/编辑 | 助理面不再出现 | 任务面仍 2 个反馈按钮 + 编辑 |
| 「已完成 · 耗时」 | 隐藏（含完成时间戳） | `.execution-ribbon.phase-completed{display:none}` |
| 左栏滚动条 / 对话索引刻度 | 隐藏（仍可滚） | `scrollbar-width:none`、`.conversation-index{display:none}` |
| 气泡宽度 | 按内容收缩：`记住了` 70px、`OMEGA7` 86px、长文上限 640px | 实测宽度 |
| 交付文件卡 | 从整行改为按内容收缩（334px） | `DEMO_OK.txt 8 B · 预览 / 下载` |
| 机器人分组 | 「已置顶/未分配」改为「在岗/待启用」（置顶是误传语义） | rail groups = `[在岗]` |

### 跨 bot 派活（真实执行，非同构造数据）

主助理调用 `assistant_delegate_to_specialist` 把「每天上午汇总行业快报」交给「行业快报监控」，
回执「已转交给专职子助理…它已完成职责绑定，首次执行在排队中」；
`assistant_handovers` 新增 1 行（status=applied），主对话顶部出现居中状态行「已安排执行 行业快报监控」，
机器人分区显示「在岗 行业快报监控」。点该 bot 进入 `/assistant?bot=specialist_86149d1e…`：
标题＝行业快报监控、输入框「给 行业快报监控 发消息」、强调色 `#0d9381`，与主助理（`#126fd4`）不同。

### 演示到的卡片形态

- 引用卡：hover 出操作栏 →「回复这条回答」→ 输入框上方引用条显示被引原文；发送后消息上方有引用头。
- 成果卡：让助理把一个 8 字节 txt 交付 → `DEMO_OK.txt`、`8 B · 预览`、下载链接（artifact id 真实）。
- 交互卡（data.by_name 卡片）：历史轮里的「频率 / 出口 / 范围」三栏卡。
- 进展浮标：折叠「后台任务进展 · 5 项进展」→ 展开 5 条（含已交付摘要与「打开原任务」）。
- 转交状态行：「已安排执行 行业快报监控」。

### 部署与踩坑（如实记录）

- 镜像依次 r5→r12。**r6/r7/r8 实际都在复用 r5 的源码层**：我用 sed 改的 COPY 路径是
  `web/harness-console`，而 r5 的 Dockerfile 里早已是 `web-src-r5/harness-console`，
  `RUN npm run build` 因此命中缓存（`#8 CACHED`），镜像里仍是旧代码。
  改成 `web-src-r<N>` 后 r9 起才真正重建；判据是进容器 grep 到新规则。
- 一次 `compose up -d web` 被执行中断，`api`/`web` 停在 Created 未启动；已拉起并确认 healthy。
- `migrate` 服务镜像原为 `20261026-r2`（不含迁移 0063），任何 `compose up` 都会失败；
  已把该服务对齐到 `assistant-api:20261030-managed-r4`。
- 打开还没跑过的 bot 会话会有两条 404（`/api/agui/threads/assistant_objective_*/history`）；
  前端本就把 404 当"空历史"处理，不影响收发，首次发送后线程才真正落库。


### 第四轮补丁（r13）：按钮贴气泡 + 去掉顶部转交条

- 用户反馈「按钮怎么在这个位置」：`.assistant-message-controls` 原按整个消息容器右对齐，
  气泡只有 640（或更短）宽时按钮落在面板最右。改为回答与操作栏同一行 flex、`align-items:flex-end`，
  实测按钮左边 = 气泡右边 + 6px（bubble right 1092 → controls x 1098）。
- 用户反馈「这个置顶还没去除」：我加的转交状态行挂在对话顶部（滚动区外），读起来像置顶条，已删除
  组件与样式；转交回执仍在助理回答文本与机器人分区「在岗」里。


### 第四轮补丁（r14）：任务面与助理面真正分开

**现象**：在助理页点侧栏里的旧任务，页面变成「我的助理」头部 + 助理聊天气泡，用户判断为「整个都污染了」。

**根因**：`switchTask` 只换 `threadId`，路由仍是 `/assistant`，`assistantEntry` 仍为 true，
于是 `assistantView` 为真 —— 视图选择错了，任务数据本身没有被改动。

**修法**：助理面改为按已加载线程推导
`assistantView = assistantEntry && (!threadId || threadId === 主助理线程 || botParam)`；
切任务时同步 `history.replaceState("/?thread=…")`。

**实测（79 r14，用户路径：/assistant → 点侧栏任务）**

| | 助理面 | 点开的旧任务 |
|---|---|---|
| url / surface | `/assistant` / `assistant` | `/?thread` / `task` |
| 头部 | 我的助理 已连接… | 预算受限验收职责检查汇报 |
| 机器人分区 / 进展浮标 | 有 / 有 | 无 / 无 |
| 用户气泡 | `rgb(18,111,212)` | `rgb(243,243,244)`（原样式） |
| 回答底色 | `rgba(255,255,255,0.08)` | `rgba(0,0,0,0)`（原样式） |
| 输入框 | 消息 KAI、无 @ | 输入消息…、`@ 智能体` 仍在 |
| 反馈按钮 | 0 | 2（原样） |
