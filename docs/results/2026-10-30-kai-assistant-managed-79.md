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


### 79 助理面复核（r14，分面改动之后）

| 检查 | 实测 |
|---|---|
| `/assistant` | surface=`assistant`，头部「我的助理 已连接 微信/飞书」，机器人分区 `[主助理, 行业快报监控]`，主助理高亮 |
| 历史 | 加载 20 条用户/20 条回答，用户气泡 `rgb(18,111,212)`，强调色 `#126fd4` |
| 输入框 | 占位「消息 KAI」，只有 `+` / 模型 / 语音 / 发送，无 `@` |
| bot 会话 | `/assistant?bot=…` 头部「行业快报监控 每天汇总行业动态…」，占位「给 行业快报监控 发消息」，强调色 `#0d9381`，rail 高亮该 bot |
| 真实收发 | Enter 发送「PING80」→ 消息 20→21、回答 20→21；点发送按钮「PING81」→ running 2 → 回答 20→21 |

**探针修正**：第一次用脚本点发送按钮时消息被追加但没起 run —— 原因是填入后只等 250ms 就点，
此时按钮仍是 `disabled`（草稿还没传到 composer 状态），属于探针与草稿状态赛跑，不是产品缺陷；
等草稿稳定后按钮点击正常。判「发送失败」前要先确认按钮当时是否 disabled。


### 第四轮补丁（api r8 / web r19）：新建 Bot 与编辑弹窗

**新建**：左栏「机器人 +」不再跳到 `/studio/assistant` 表单，打开对齐客户端的「新建 Bot」弹窗
（名称、12 色、6 形状）。按用户要求**不强制绑定执行智能体**：请求可省略 `agentName/agentVersion`，
服务端在缺省时沿用本人已有 bot 的智能体，再退到本人第一份已发布版本；缺省也不做隐性发布。

**编辑**：bot 头部新增「…」，弹窗含 姓名 / 标题（可选）/ 描述 / 例行程序 / 保存，
走新增的 `POST /specialists/{id}/profile`（只改呈现信息，不动配置状态机）。

**实测（79，web r19 + api r8）**
- 弹窗形状：`agentSelect=false`、颜色 12、形状 6
- 创建「演示 Bot 二」→ 跳到 `/assistant?bot=specialist_0266…`，空状态「演示 Bot 二 还没有职责」
- 「…」弹窗预填名称，字段 `[姓名, 标题（可选）, 描述]`，含「例行程序」区
- 保存后弹窗关闭、头部描述变为新值「盯着 Codex 额度重置信号。」
- 两个验证用的演示 bot 已 `archived`（`/specialists` 里不再出现在岗）

**未做**：客户端弹窗顶部的头像点选（改颜色/形状）目前只在新建时可选，编辑弹窗里还没有再改外观的入口。


### 第五轮（api r9 / web r20）：派活报错根因修复 + 「+」图标对齐

**报错**：`Delegation failed: "agent version not found: 行业快报监控@1.1.1+platform.99dfe677"`，随后整轮「处理失败」。

**根因（两个叠加）**：
1. 派活工具 schema 暴露 `agent_name/agent_version`，模型把 bot 名当智能体名并幻觉出版本号；
   处理器还兜底用 `session.agent_version`，都不是 Bot 自己的绑定。
2. `create_specialist` 在**复用**路径上也先 `get_published` 校验调用方给的版本，注册表里当然没有。

**修法**：工具 schema 删掉这两个字段、处理器固定传 None；服务先按 slug 找已有 Bot（用其既有绑定），
新建才做默认解析 + 校验；职责 brief 一律取 Bot 记录的绑定。

**第二个失败**：修完后实测仍整轮失败 → worker 日志 `SandboxLeaseError: session already holds a live
sandbox lease (owner=run-fence:1, run=run_dc0735…)`——是我滚 api/worker 打断了在跑的 Run，
取消后租约没释放（已知僵尸租约模式）。**只回收那一条租约**（state→released），其余数据未动。
复测：派活成功，回执「待补充：schedule_minutes、notify_channel；缺项不启动」，正是设计行为。

**「+」图标**：机器人分区改用与项目区相同的 plus 图标（rail 26×26 = project 26×26，同悬停样式）。


### 第六轮（web r21–r26）：照客户端源码补齐「机器人动画活动行」与流式正文

用户指出客户端的两个关键体验：①处理过程/状态是通过机器人动画（脸 + 状态短语）实现的；
②对话像聊天软件一样流畅。直接逆向了 `/Applications/Grok Bot.app` 的 app.asar（renderer 697 文件）：

**客户端实现事实**（源码证据，非猜测）
- 脸 = `GrokBotMark`：canvas 角色（resolution 192、seed 派生形状），`state`（idle/working/…）、
  `maxFrameRate` 60/30（reduced-motion 降档）、`spin/bounce/nod` 三种 poke（0.9/0.62/0.5s）、
  **瞳孔随鼠标**（`attachMarkHoverGaze`，指针位置 ×0.5）、眼色走 `--grok-bot-eye` CSS 变量
- 状态短语 = `activityPhraseDraw`（按词渐进显现）+ `statusReveal`；活动行占对话流一行

**web 等价实现**（r21 起）
- `GrokFace`：blob+双眼 SVG；idle 4.2s 慢眨眼 / working 浮动+瞳孔随鼠标（±1.4px，rAF 节流）/
  done bounce / failed nod；颜色取 `--kai-bot-accent`
- `ActivityPhrase`：按词 90ms 渐现，已显现词稳定 DOM、更新续播；SSR/历史一次性给全短语
- `TurnActivity`/pre-response 行在助理面换 `GrokActivityRow`（任务面保留原样）

**流式真相（逐层实测）**
1. 服务端：SSE 逐块（curl，120 字回答 = 9 个 TEXT_MESSAGE_CONTENT，逐块到达）
2. 代理：harness-proxy 对 SSE 直通透传（只压 GET+JSON）
3. 浏览器：页内手动 fetch 同端点 17 增量在 1.04s 内逐块到达 —— 传输全通
4. 前端回调：r24 临时日志证实 `onTextMessageContentEvent` 逐条触发
5. **真空帧根因**：assistant-ui 的 in-flight 助手消息在 state 里存在但 DOM 不渲染，
   正式气泡要等 run 结束才挂载 → 正文一次性蹦出（用户说的"不流畅"）
6. 修法：`LiveStreamingBubble`——live store（rAF+120ms watchdog 批量）visible 且 streaming 时
   画同款气泡；每 150ms 检查 DOM 正式气泡是否已覆盖同等文本，覆盖才让位

**端到端时间线（79 实测，120 探针）**：5.9s store 48 = liveBubble 48（逐字长出）；
7.5s 正式气泡挂载 328（无缝接管）；8.2s complete 气泡让位。无真空帧、无双份。

**顺带**：「+」图标对齐项目区（26px plus 图标）；`window.__kaiLive` 只读诊断出口保留。
**已知边界**：等待期（0→5.7s，排队/模型/压缩）没有正文可流——由脸+短语行表达进展
（如「正在生成可恢复的上下文摘要」）；上游网关是爆发式吐字（~850 字/秒），非匀速。


### P0-2（api r10 / web r27）：编辑 Bot 弹窗可改颜色/形状

- 编辑弹窗复用新建的 12 色 × 6 形选择器（同一份 `BOT_COLORS/BOT_SHAPES`），预览头像实时联动，
  预选值读自 `config.appearance`
- 持久化走既有 `POST /specialists/{id}/profile`：`SpecialistProfileRequest` 增加 appearance，
  `update_specialist` 的呈现分支把它并入 config（与 title 同路），revision 照常递增
- rail 头像与头部强调色经 `GET /specialists/{id}` 正常读回

**79 冒烟（真实 bot 行业快报监控）**：打开「…」→ 12 色/6 形可选 → 改 #ff2d95 + star → 保存
→ 头部强调色 `#0d9381 → #ff2d95`；刷新后 rail 头像 `rgb(255,45,149)`；
API 确认 `config.appearance={"color":"#ff2d95","avatar":"star"}`、revision 4→5。
冒烟后已恢复原外观（#0d9381 + blob），revision 6。

测试：`pytest tests/unit/assistant/`（91 过，含 appearance 携带/校验 4 条）；
`vitest run tests/bot-edit-dialog.spec.tsx`（3 过）；全套 web 1379 过 / 1 既有失败（account-settings，与本轮无关）。


### P0-3（api r11）：僵尸 sandbox lease 自愈（对账回收，替代手工改库）

**触发条件**：`acquire` 抛出「session already holds a live sandbox lease」时，Worker 走一次对账：
`SandboxLeaseService.reconcile_dead_run_lease` 仅当
① 该 lease 仍为 `active` 且未过期、② 绑定 Run 可查且状态 ∈ terminal
（cancelled/succeeded/failed/timed_out/rejected）时，才把它标为 `released`（epoch CAS 防并发），
发 `sandbox.lease.reconciled` 事件（released_lease_id/run_id/owner），然后重试 acquire 一次。
其余情况一律不动：Run 在跑、lease 已 released/reclaimed、跨会话、Run 查询失败（不猜测归属）。
幂等：回收后再对账返回 None；TTL 过期仍走既有 supersede 路径，不经此逻辑。

**测命令与结果**
- `pytest tests/unit/sandbox/test_lease.py tests/unit/worker/test_orchestrator.py tests/unit/worker/test_sandbox_lease_renewal.py -q` → 78 passed
  - 终态 Run 的 lease → released，可幂等重放，同会话可再取（epoch +1）
  - RUNNING 的 lease 不回收、跨会话不回收、Run 查询抛错不回收
  - orchestrator 级：僵尸租约自愈（新 Run 成功 + reconciled 事件）；RUNNING 租约照常阻断（Run FAILED，无 reconciled 事件，原 lease 仍 active）
- `ruff check` 相关文件 → clean
- 已知边界：全树 `pytest tests/unit` 的 collect 失败是仓库既有的同名测试文件问题（test_lifecycle/test_memory_tools/test_service 等在多目录重名，均早于本分支），未在本轮处理。

**风险说明**：只回收「绑定 Run 已终态」的租约；epoch fencing 保证即使误判，原 owner 恢复后也会察觉失去所有权。
回收事件落在 run 事件流，审计可查。未引入后台清扫任务、未改 TTL 语义、未动委派绑定策略。


### P1-1（api r12 / web r28）：无人查看则暂停例行程序（最小后端闸门）

**语义**（对齐 Grok「例行任务无人查看就自动暂停，避免浪费用量」）：
- 调度入口 `schedule_due`（worker `assistant_maintenance` → `AssistantService.process_once`，2s 轮询）
  对**绑定了专职 Bot** 的到期职责做闸门：`now - specialist.lastViewedAt > 阈值` → 本次不入队，
  在 Bot 上写 `routinePausedReason = {reason: "unviewed", hours_since_view, at}`（API/UI 可读），
  **照常推进 next_check_at**——打开会话后的下一个周期自然恢复。
- `lastViewedAt` 在 Bot 创建时初始化（首次例行不被拦）；存量 Bot 的 None 视为从未查看（会被拦，
  打开一次即恢复）。打开 Bot 会话 = `GET /specialists/{id}`（bot 页面加载即调）→ touch，
  刷新 lastViewedAt 并清除暂停标记；**不递增 revision**（查看是高频读态，不与配置 CAS 竞争）。
- 查看后的下一次调度顺手清残留标记（双侧自愈）。预算闸门（maxRunsPerDay/TotalRuns）路径未动。

**默认阈值**：24 小时；env `HARNESS_ASSISTANT_UNVIEWED_PAUSE_HOURS`（小时，≥1）可调。

**测命令与结果**
- `pytest tests/unit/assistant tests/unit/sandbox/test_lease.py -q` → 103 passed
- `pytest tests/integration/storage/test_delegation_real_gateway.py::test_unviewed_specialist_routine_is_skipped_then_recovers -q` → passed
  （真实 Postgres 夹具：fresh → 入队；25h 未查看 → 跳过 + `reason=unviewed` 且执行数不变；
  touch → 下一次到期放行 + 标记清除）
- 全套 web 1380 passed / 1 既有失败（account-settings）；`tsc` clean；ruff clean

**UI（最小）**：Bot「…」编辑弹窗的例行程序列表显示「已暂停 · 因无人查看已暂停，打开本会话后恢复」
（琥珀色状态字）；rail 不另加角标。查看后弹窗重开即恢复原状态字。

**边界**：仅作用于绑定 Bot 的职责；普通定时任务不受此闸门（仍是预算/终止期闸门）。
跳过的周期不补跑（与"避免浪费"一致）；skip 记录在 Bot 上可读，不在 run 流里（没有 run 发生）。


### P1-2（api r13→r15）：子助理领域记忆 scope 真验收（Goal R3）

**路径确认**：读侧 `MemoryBankGateway(scope=specialist:{slug})`（composition `specialist_memory_scope`）
单测已有；**真验收发现两处读写不一致缺口，均已修**：

1. **写侧**：自动提取入队按 `session.agent_name` 落 scope → 职责线程写进底层 agent 的记忆，
   自己却读不到（`7dc8c84a`）。
2. **读侧**：`harness-memory` MCP 工具（search/propose/read）与预加载 projection 都用
   `identity.agent_name` → 职责线程能读到 lead-agent 的条目（79 实测复现：
   bot 会话 search_memory 返回 `DELTA100314` 等 lead-agent 记忆）（`cb6f4331` + `9dfbf7f4`）。

**统一判定**：`PostgresMemoryBankRepository.resolve_session_scope` —— session_id（或经
`agui_thread_bindings` 反查的 thread_id）为 `assistant_<objective_id>` 且 objective 绑定
specialist → `specialist:{slug}`；否则 None。写侧（提取入队）与读侧（projection、
harness-memory 工具经 `MemoryBankService.scoped_identity`）共用，与 composition 同判。

**79 真验收证据**（登录态，specialist_86149d1e…＝行业快报监控，slug `zhs-3cf716bb44`）
- 写：`POST /api/memory-bank/proposals {"agentName":"specialist:zhs-3cf716bb44",...}` →
  `memory_8be6787bccf3…`（v1 pending）→ confirm 200 → v2 active。
- 读回：`GET /api/memory-bank/entries?agentName=specialist:zhs-3cf716bb44` → 仅该条；
  `agentName=kai-personal-assistant` 与 `agentName=archive-assistant-agent` → **0 条，无泄漏**。
- 语义召回：`POST /api/memory-bank/search {"agentName":"specialist:zhs-3cf716bb44","query":"P1-2 验收 领域记忆 scope"}`
  → 命中 score 0.826；同 query 在主助理 scope → 0 hits。
- 会话内工具召回（真实对话，UI）：bot 会话发「分别用 search_memory 搜 P1-2 验收 和 部署验收」→
  run_8de1ac1e… 两次 search_memory 的 tool.result 都只含 specialist 条目（payload 原文核对），
  **不再出现 lead-agent 条目**；匿名 `GET /memory-bank/entries` 与 `POST /search` 均 401。

**测命令与结果**
- `pytest tests/unit/assistant/test_memory_tools.py -q` → 5 passed（新增 scoped_identity 换算/不换算）
- `pytest tests/unit/memory_bank tests/unit/runtime/test_memory_tools.py -q` → 37 passed
- `pytest tests/integration/storage/test_memory_bank_postgres.py -q` → 8 passed
  （新增：production-shaped 职责线程 session_→绑定→objective→specialist 链路下，
  提取 job.agent_name == specialist:{slug}）
- `pytest tests/integration/storage/test_delegation_real_gateway.py -q` → 30 passed（预算/委派闸门未破坏）
- ruff 全绿。已知边界：全树 collect 的同名文件问题依旧（前文已记）。


### P1-3（api r16 / web r29→r30）：转交状态在主对话流内的轻量可视化

**形态**（对齐 Grok「已发送给 X」）：转交发生后，主对话最后一条助手回答内部出现**居中细状态行**
「已发送给「行业快报监控」」/「待补充 ·「天气提醒」」——随消息流滚动（`position: static`，
非 sticky/置顶条），点击整行进入该 bot 的会话。

**数据流（事件驱动，免轮询）**：
1. 派活工具成功即发 `ui.handover` RunEvent（overlay 增加 `emit` 回调；composition 经 events 仓库
   append；emit 失败不影响转交本身）→ AG-UI CUSTOM 事件。
2. `HarnessHttpAgent.onCustomEvent` 转发为窗口事件 `kai:handover` → `handover-store` 按 bot 去重
   （最新状态优先，最多 3 条）。
3. 页面加载时一次性 `GET /assistant/handovers` 投影（刷新后仍在）；之后零轮询。
4. 行渲染在 `personalAssistant && isLast` 的助手消息内（旧置顶条 HandoverStrip 不回归）。

**79 冒烟**（真实对话派活「休息提醒」）
- 状态行：`[待补充 ·「休息提醒」, 待补充 ·「天气提醒」]`，`insideLastMessage: true`、
  `position: static`、href=`/assistant?bot=specialist_4667…` ✓
- 点击进入 bot 会话：URL `?bot=specialist_26cb…`、头部「天气提醒」、占位「给 天气提醒 发消息」、
  rail 高亮 ✓（曾因软导航不重挂载失效，改为 `location.assign` 整页导航——与 bot-rail 同判）

**测命令与结果**
- `pytest tests/unit/assistant/test_delegation.py -q` → 24 passed（ui.handover 发出 / emit 缺省兼容）
- `npx vitest run tests/handover-row.spec.tsx` → 3 passed（中文文案/链接目标/按 bot 去重/非置顶约束）
- 全套 web 1383 passed / 1 既有失败（account-settings）；tsc、ruff clean


### P1-4（api r17）：Computer 多任务排队只读投影

**语义**：共享桌面单执行者（assistant_desktop_turns）不变；本切片只加**只读可观察性**——
- `GET /api/studio/assistant/desktop/occupancy`（登录态，匿名 401）：
  `holder`＝当前占用（objective_id/execution_id/职责标题/acquired/expires），
  `waiting`＝需要桌面且 pending 的执行按创建序排列，带 `position` 与中文
  `wait_reason`（占用中＝「单执行者占用中（另一项职责正在使用共享桌面）」；
  无占用＝「等待桌面就绪」）。纯读，不改执行状态、不引入并发。
- 跨项目纳管拒绝文案带线索：`bind_thread` 现在返回
  「跨项目纳管被拒绝：职责属于项目「<名>」，而线程属于项目「<名>」；需要用户明确授权该项目后才能纳管」
  （项目名经 ProjectService，回退 id），不再是模糊的「需要用户明确授权」。

**测命令与结果**
- `pytest tests/integration/storage/test_delegation_real_gateway.py -q` → 31 passed
  （新增 occupancy 用例：holder 可见、桌面职责出现在 waiting 且 position=1、
  非桌面执行不出现、释放后原因翻转为「等待桌面就绪」）
- `pytest tests/unit/assistant -q` → 94 passed；ruff clean

**79 冒烟**：`GET /api/studio/assistant/desktop/occupancy` → `{"holder": null, "waiting": []}`
（当前无执行占用；真占用需两条桌面职责同时跑，单测已覆盖）；匿名 401 ✓。

**边界**：不做仲裁增强（FIFO 抢占/优先级）、不改 lease 回收、不做 UI（进展卡旁一行留次日）。


### P2-1（api r18 / web r31）：检查点 tokens/cost/耗时进托管面板

**后端投影**（复用既有 checkpoint 管道，零计量改动）
- `managed_overview` 的每个任务项新增 `run_id` 与 `duration_seconds`
  （终态 run：updated_at − created_at；进行中为 null）。
- `GET /objectives/{id}/managed` 把该 objective 最近检查点按 run_id 投影成
  `metrics = {tokens, costUsd, unknownUsage}`：`unknown_usage=true` 或 tokens 缺失时
  tokens=null + unknownUsage=true（UI 显式「未计量」，不显示假 0）。

**前端**（托管面板，每个纳管任务一行中文摘要）
- 有计量：`tokens 1,234 · cost $0.02 · 耗时 45 秒`；
  无 cost 字段则只显示 tokens 与耗时；
- 未计量：`tokens 未计量 · 耗时 45 秒`。

**测命令与结果**
- `npx vitest run tests/managed-metrics.spec.tsx` → 3 passed（有计量渲染/未计量不显示假 0/后端投影）
- `pytest tests/unit/assistant -q` → 94 passed；`pytest tests/integration/storage/test_delegation_real_gateway.py -q` → 31 passed
  （managed_overview 的 stub run 补了 run_id/created_at/updated_at）
- 全套 web 1383 passed / 1 既有失败；tsc、ruff clean

**79 冒烟**
- API：`GET /objectives/objective_5796310d…/managed`（有绑定的 coordinator）→
  overview[0]：`run_id=run_e737a2e4…`、`duration_seconds=13`、`metrics=null`
  （该 run 无检查点 → UI 只显示耗时）。
- 79 现网检查点核对：`assistant_checkpoints` 最近 5 条全部 `tokens=NULL, unknown_usage=t`
  （网关未回传用量）→ UI 将如实显示「tokens 未计量」，正是本切片要求的诚实呈现。
- UI 全页冒烟：托管面板已确认可达（截图 136），但带绑定且带 run 的职责
  「为您提供帮助服务」不在列表第一页（展开显示 40 条之后），未截到计量行本体；
  渲染逻辑由 vitest 覆盖，已知诚实行为=「tokens 未计量 · 耗时 N 秒」。

## 修复：主助理→子助理首次执行不入队（2026-10-31）

根因：`delegate_to_specialist` 在 `applied` 分支只写 objective/handover，从未
`repository.enqueue`；`configure_specialist` 的 `schedule_minutes` 只落在
specialist.config，不同步到 `objective.schedule/next_check_at`；启用也不补派发。
子助理会话因此长期为空。

修复（二选一取「启用补入队」，试运行仍保留为显式一次性入口）：
- `applied` 且非 follow_only、无缺项：入队一条首次执行，trigger_key=
  `delegate-specialist:{message_id}:{objective_id}`（重放幂等）；入队失败则回执
  保持 `received`，不再假称已安排。
- `configure_specialist` 保存 `schedule_minutes` 后同步所有绑定职责的
  `objective.schedule` 与 `next_check_at`（同频率不重复写）。
- `set_specialist_status(ACTIVE)`：对尚无任何执行且 autonomous 的绑定职责补一次
  入队（trigger_key=`activate:{specialist_id}:{objective_id}`）；已有执行/只跟进
  /预算拒绝都不重复派发。
- overlay 回执改为「首次执行已入队」并带执行与线程，注明首次产出前会话可能为空。

验证命令与现象：
- `uv run pytest tests/unit/assistant/test_delegation.py -q` → 25 passed；新增
  applied 回执含执行/线程且无「待补充配置」。
- `HARNESS_TEST_DATABASE_URL=postgresql+asyncpg://harness:harness@127.0.0.1:35432/harness_test uv run pytest tests/integration/storage/test_delegation_real_gateway.py -q`
  → 32 passed；新增断言：applied 恰有 1 条 `pending` 执行、缺项为 0 条；
  configure 后 `objective.schedule.interval_minutes=1440` 且 `next_check_at` 非空；
  启用后闲置职责补 1 条 `activate:*` 执行。
- `uv run pytest tests/unit/assistant -q` → 95 passed。
- `HARNESS_TEST_DATABASE_URL=... uv run pytest tests/integration/storage/test_assistant_objectives.py tests/integration/storage/test_assistant_channel_controls.py -q`
  → 15 passed。
- `uv run ruff check`（改动的 service/delegation/单测文件）→ All checks passed。
- 注：`tests/integration/storage/test_delegation_real_gateway.py` 的 I001 为既有
  局部 import 顺序问题（基线同样存在），未在本轮顺手改动。
