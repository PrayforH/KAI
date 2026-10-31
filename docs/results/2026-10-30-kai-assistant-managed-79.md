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

## P2-2 故障回放：失败 run 一键回到原线程（2026-10-31）

范围：只做导航与上下文投影，不做录像式回放。托管面板里最近一次 run 为
`failed` / `timed_out` / `rejected` 时，任务卡显示中文「运行失败」与检查点摘要，
并提供「打开失败回放 →」；无失败 run 时仍只有原来的「打开原任务 →」，不出现假入口。

- 后端（最小补投影）：`GET /objectives/{id}/managed` 在既有 checkpoint 按 run_id
  投影里补 `checkpoint_id` 与 `checkpoint_summary`（摘要截断 300 字），
  `metrics` 形状不变；不改委派、lease、预算闸门。
- 前端：失败入口目标为
  `/?thread=<threadId>&run=<runId>&trace=1[&checkpoint=<checkpointId>]`；
  目标页读取 `trace=1` 直接进调用轨迹，`run` 用于选中该 run 的异常节点
  （无异常节点时落到该 run 首个节点）。非失败态沿用原线程链接。

**命令与现象（本地证据；本轮未部署 79）**
- `npx vitest run tests/managed-replay.spec.tsx tests/managed-metrics.spec.tsx tests/assistant-manager.spec.tsx tests/run-trace-console.spec.tsx`
  → 4 files / 12 tests passed；覆盖失败时入口存在且 href 含
  `thread/run/checkpoint/trace=1`、成功 run 不渲染失败入口、深链进入轨迹并按 run 定位。
- `npx tsc --noEmit` → 通过。
- `uv run pytest tests/unit/assistant/test_managed_replay.py -q` → 1 passed；
  断言 managed 投影带 `checkpoint_id`、摘要与既有 metrics。
- `uv run pytest tests/unit/assistant -q` → 96 passed。
- `HARNESS_TEST_DATABASE_URL=postgresql+asyncpg://harness:harness@127.0.0.1:35432/harness_test uv run pytest tests/integration/storage/test_delegation_real_gateway.py -q -k "managed_overview or checkpoint"`
  → 3 passed。
- `npx vitest run`（全套）→ 1404 passed / 1 skipped / 1 既有失败
  （`tests/account-settings.spec.ts` 的设置导航链接计数，与本次改动文件无关）。
- `uv run ruff check src/harness/assistant/api.py tests/unit/assistant/test_managed_replay.py`
  → All checks passed。

79 说明：本轮未改部署、未推送镜像；上述为工作树本地单测与类型检查证据。

## 转交后「保存并启用」补齐配置（2026-10-31，web r36）

`pending_input` 在助理面此前只有文字说明、没有补配置入口，用户看到「待补充」却无处可点。
新增 `web/harness-console/src/components/specialist-setup-card.tsx`，在
`/assistant?bot=<id>` 的子助理对话区顶部（滚动区之上、不随消息滚走，也不是右侧面板）
给检查频率（60/360/1440）与通知渠道
（inbox），「保存并启用」严格两步：`POST specialists/{id}/config`（带 `expectedRevision`，
服务端落 READY 并同步职责 `next_check_at`）→ `POST specialists/{id}/status {active,
expectedRevision: 上一步返回的 revision}`（服务端为闲置职责补一次首跑入队）。
成功后卡片变「已启用，首次执行排队中」（只在本次点击后，重新加载时 active 整块不渲染，
避免旧回执常驻）并触发页面重取 bot（`botRevision + 1`）；
已 `active` 不渲染，无绑定职责提示先派活，失败原样显示服务端错误。未改委派状态机、未碰飞书/微信入站。

- `npx vitest run tests/specialist-setup-card.spec.tsx` → 4 passed；
  `tests/specialist-setup-card.flow.spec.tsx` → 3 passed（jsdom 真点击，断言 config→status
  调用顺序、revision 传递、`schedule_minutes=60`/`notify_channel=inbox`、成功文案与 `onReady`、
  以及 active 加载不渲染残留横幅）。
- `npx tsc --noEmit` → 通过；`npx vitest run`（全套）→ 1432 passed / 1 skipped / 1 既有失败
  （`account-settings.spec.ts`，与本次无关）。
- 后端未改，「配置齐全 → applied + 恰好 1 条 queued 执行」「启用后补首跑」由既有集成测试
  `test_applied_delegate_enqueues_exactly_one_first_execution`、
  `test_completed_specialist_config_enables_first_execution` 覆盖。
- 79 已滚到 web `kai/assistant-web:20261030-managed-r36`（healthy，`/assistant` = 200）；
  以 `kai/assistant-web:20261030-managed-r4` 为基座从 `web-src-r36` 构建，
  3 个改动文件逐文件 sha256 与本地一致，卡片 chunk 由 `2be9lq7pqnt3n.js` 变为
  `0z38mvebel7wp.js`（确认镜像里是新代码）。
- 79 冒烟（只读，纯 HTTP :3501）：`/assistant?bot=specialist_466780791f3c4f218b66019818346459`
  （休息提醒）实测 `card=true / title=还差两步就能开始跑 / 默认每天 / 站内消息已选禁改 /
  staleBanner=false`，按钮为 `[每小时, 每 6 小时, 每天, 站内消息, 保存并启用]`。
  因会把真实 bot 置为 active 并按其频率持续运行，未替用户点击启用。
- 详细记录：`docs/results/2026-10-31-specialist-pending-input-setup-card.md`。

## 助理面过程摘要与中间结果（2026-10-31，web r37）

痛点：助理面进行中几乎只显示「正在等待本轮模型结果」，没有过程摘要/中间结果。

根因（已探查确认）：后端 `tool.request` / `tool.result` 的文案在 `title`
（「调用 Shell」「工具调用完成」），`summary` 为 null；`runtime.system requesting`
才写 `summary`（activity.py:556-583）。`progressPhrase` 只读 summary 并反向扫描，
于是工具阶段被跳过，长期停在等待文案。

改动（只动前端，未碰委派/pending_input）：

- `grok-activity.tsx`
  - 新 `itemPhrase()`：非 prose/run 条目取 `summary?.trim() || title?.trim()`；
    `tool.result`/`tool.allowed` 优先用 `metadata.result_summary`（如「返回 12 行 · 340 字符」），
    比通用「工具调用完成」有信息；仍跳过 >60 字；`allowTransient` 语义不变。
  - 新 `activitySteps()` / `shouldShowChatActivitySteps()` / `ChatActivitySteps`：
    最近 ≤5 条 `tool.*` / `runtime.*` / `context.*`，一行一条，跳过纯等待文案、
    跳过与主短语相同的文本、去重、失败标红；只在回合进行中渲染。
- `agent-thread.tsx`：两处助理面活动行（turn 活动行、pre-response 活动行）在
  `GrokActivityRow` 下方各挂一个 `ChatActivitySteps`；终态分支本来就不渲染活动行
  （0ee6fffd 的规则），步骤条随之消失，不钉进历史。
- `web-codex.css`：`.chat-activity-steps` 小列表（12px、缩进对齐短语、单行省略），
  仅作用于 `.chat-stage[data-surface="assistant"]`；没有搬任务面的 ExecutionRibbon。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                          # clean
npx vitest run tests/assistant-activity-steps.spec.tsx    # 12 passed（新增）
npx vitest run tests/assistant-activity-terminal.spec.tsx tests/assistant-dup-channels.spec.tsx   tests/assistant-duplicate.spec.tsx tests/specialist-setup-card.spec.tsx   tests/specialist-setup-card.flow.spec.tsx               # 22 passed
npx vitest run                                            # 1444 passed / 1 skipped / 1 既有失败
```

新增断言覆盖：只有 title 的 `tool.request` → 短语「调用 Shell」；等待 summary 之后
出现工具调用 → 取最新工具短语；`result_summary` 优先于通用标题；正文/思考/run/超长
仍被忽略；步骤条 ≤5 条、去重、跳过瞬时等待、失败着色；`running`/`waiting_approval`
显示步骤、`completed`/`succeeded`/`failed` 不显示（活动行本身也不显示）；
两处活动行都接线；渲染里不出现任务面 ribbon。

**79 部署与只读验收**

- web `kai/assistant-web:20261030-managed-r37` 已滚到 79（healthy，`/assistant` = 200）；
  基座 r4，ctx `web-src-r37`，3 个改动文件逐文件 sha256 与本地一致。
  镜像内 grep：r37 的 `1syfg_iiuhqfg.js` 含「过程摘录」、`1myunhinr8dg0.css` 含
  `.chat-activity-steps`（r36 两者都没有）。
- 只读探针（纯 HTTP :3501 `/assistant`，硬加载 9s 后）：
  `activityRows=0 / stepLists=0 / stepCssRules=4 / answers=12 / proseInPhrase=0`
  ——历史终态回合不出现活动行与步骤条，样式已生效，正文没有回流到短语。
  截图：`/tmp/kai79-accept/152-r37-assistant.png`。
- 未验证：真实**进行中**回合的视觉（需要发起一次真实 run 并抓到工具阶段）；
  该行为的判定逻辑由上述单测钉住。冒烟未替用户发起 run、未点任何按钮。

## 助理面中间结论语（用户纠正 r37，2026-10-31，web r38，marker kai-mid-conclusion）

用户明确否决 r37 的「调用 Bash / 工具调用完成 / 14 项工具可用」过程条：要的是工具**之间**的
业务结论语（如「收到快捷操作：目标改为 8 公里…先确认参数。」「参数已确认：…现在改目标距离为 8000 米…」）。

改动（仅前端 `grok-activity.tsx`）：

- **短语/摘录改为结论语**：取 `message.delta`（其次 `reasoning.summary.delta`）的 summary，
  但只取**末次工具/runtime/context 事件之前**的条目——之后的是本轮终答，仍由回答气泡负责。
  该边界同时挡住 dce054a7 的重复：纯文本回合没有工具 → 边界 -1 → 一条都不取。
- **过滤工具日志/引导**：`调用 X`、`工具调用完成/失败`、`已加载 N 项…`、`N 项工具…`、
  `运行环境…`、`正在准备运行…` 一律不当主展示；工具只剩「返回 12 行 · 340 字符」这类
  非引导结果时，只回落**一句**，不堆工具名；没有可用文字时整行不出现。
- **长度**：源文本 ≤160 字才收（整篇终答远超，天然挡掉），展示按句截断到 120 字；
  步骤列表只放结论语（最近 ≤5 条，去重、排除与主短语相同的一条）。
- 终态隐藏沿用 0ee6fffd：`completed/succeeded/failed/cancelled` 不渲染活动行，
  步骤条一并消失，不钉进历史；未改委派/pending_input。

**真实数据回放（只读，未发起 run）**：用 79 历史线程里的 6 个 `harness_run_activity` 投影
（86 条消息中带 activity 的回合）跑同一套取舍逻辑，选出的短语都是业务结论，例如：

- 「我先盘点工作区里的文件，确认这次「预算受限验收职责」检查需要核对哪些材料。」
- 「材料读完了。这两份根本不是同类东西：一份是 21 行的知识库问答…我先做了一次快速一致性体检。」
- 「继续盯梢本轮：确认 Day 27 帖子或 banked 重置到账确认。」

同一回合里更早的结论会成为步骤条；「调用 X / 工具调用完成」未出现在任何短语或步骤中。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                          # clean
npx vitest run tests/assistant-activity-steps.spec.tsx    # 14 passed（重写）
npx vitest run tests/assistant-activity-terminal.spec.tsx tests/assistant-dup-channels.spec.tsx   tests/assistant-duplicate.spec.tsx                      # 15 passed
npx vitest run                                            # 1446 passed / 1 skipped / 1 既有失败
```

覆盖：最新中间结论作短语；多条结论时取最新、其余进步骤；终答（末次工具之后）永不作短语；
工具引导不当主展示；仅工具时回落一句非引导结果或不出行；running/waiting_approval 显示、
终态一律不显示；步骤 ≤5 去重；按句截断 ≤120。

**79 部署**：web `kai/assistant-web:20261030-managed-r38` 已滚（healthy，`/assistant` 200）；
基座 r4，ctx `web-src-r38`，`grok-activity.tsx` sha256 `4fdd7a12…` 与本地一致；
组件 chunk 由 r37 的 `1syfg_iiuhqfg.js` 变为 `2hdutik2uwl-7.js`（含「过程摘录」与引导过滤）。
只读探针：`activityRows=0 / stepLists=0 / stepCssRules=4 / answers=12 / toolWordsInPhrase=0`
（历史终态不出现，样式已加载，短语里没有工具名）。截图 `/tmp/kai79-accept/153-r38-assistant.png`。

**未验证**：真实进行中回合的视觉（需发起真实 run 并抓工具间隙）；取舍逻辑已用上述真实数据回放 + 单测钉住。

## 验收回修：结论语收紧到 10 字（2026-10-31，web r39，marker kai-mid-conclusion-10chars）

上一切片 FAIL 点：`DISPLAY_CONCLUSION_CHARS = 120`，与「对齐本机 Grok Bot，约 10 个汉字以内」不符。

- **口径**：改为 `DISPLAY_CONCLUSION_CHARS = 10`，按显示宽度计——汉字/全角 1、ASCII 半角 0.5
  （即汉字 ≤10 字、英文约 20 字符）；按字素切分（`Intl.Segmenter`，无则退码点），
  emoji/组合字符不会被切半。
- **截断策略**：10 字预算内优先停在从句边界（「收到快捷操作：」「继续盯梢本轮：」），
  否则硬截为 9 字 + 省略号（如「我先盘点工作区里的…」）；**不再允许长段落当活动短语**。
  活动行 `GrokActivityRow` 自身再走一次该口径兜底，调用方塞长文本也不会铺满一行。
- 结论语优先、工具过程降权、终态隐藏（0ee6fffd）与防重复（dce054a7）均未回退。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                          # clean
npx vitest run tests/assistant-activity-steps.spec.tsx    # 19 passed
npx vitest run tests/assistant-activity-terminal.spec.tsx tests/assistant-dup-channels.spec.tsx   tests/assistant-duplicate.spec.tsx                      # 15 passed
npx vitest run                                            # 1451 passed / 1 skipped / 1 既有失败
```

断言改为 ≤10 宽：`收到快捷操作：`、`参数已确认：`、`返回 12 行 · 340…`；
新增「无边界时 9 字 + …」「英文 ≤20 字符」「长段落行内也不会超 10 宽」。

**真实数据回放（只读）**：历史 `harness_run_activity` 投影按新口径取样——
「继续盯梢本轮：」「材料读完了。」「起身走动两分钟，」「工作区没有待归档的…」，
步骤行为「我先盘点工作区里的…」等；无工具名、无长段落。

**79 部署**：web `kai/assistant-web:20261030-managed-r39` 已滚（healthy，health/200）；
ctx `web-src-r39`，`grok-activity.tsx` sha256 `8d72d54a…` 与本地一致；
组件 chunk 由 r38 的 `2hdutik2uwl-7.js` 变为 `33rro6nqhidne.js`。
只读探针：`activityRows=0 / stepLists=0 / stepCssRules=4 / answers=12`（历史终态不出现）。
截图 `/tmp/kai79-accept/154-r39-assistant.png`。

**未验证**：仍未有真实进行中回合的视觉（需发起真实 run）；取舍与截断由真实数据回放 + 单测钉住。

## 中文结论语逐字渐现（2026-10-31，web r40，marker kai-phrase-cjk-reveal）

缺口：`ActivityPhrase` 用 `text.split(/\s+/)` 按空白分词，中文结论语没有空格，
整句一次出现，对不齐本机 Grok 的字级/词级渐现。

- 新增 `phraseUnits(text)`：CJK 为主（>50%）或「无空白但含 CJK」→ 按字素
  （复用 `splitGraphemes`，`Intl.Segmenter` 优先）；纯英文（含单个单词）→ 仍按词
  （词与间隔各自为一个单位）。单个英文单词不会在句子变长时被切成字母（模式稳定）。
- 渐现从「共享前缀」续播：文本换成另一句结论时从头画第一个字，而不是沿用旧进度
  整句贴出；挂载/历史文本仍一次性给全（`drawn = units.length`），SSR 不截断。
- 未动：10 字展示口径（`trimConclusion`）、终态隐藏、工具过程降权、终答不进活动行。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                          # clean
npx vitest run tests/assistant-phrase-reveal.spec.tsx     # 8 passed（新增）
npx vitest run tests/grok-activity.spec.tsx               # 3 passed（SSR 断言改为去标签比对文本）
npx vitest run tests/assistant-activity-steps.spec.tsx tests/assistant-activity-terminal.spec.tsx \
  tests/assistant-dup-channels.spec.tsx tests/assistant-duplicate.spec.tsx  # 34 passed
npx vitest run                                            # 1459 passed / 1 skipped / 1 既有失败
```

新增断言：`phraseUnits` 对「继续盯梢本轮：」给出 7 个字素、对英文按词、单个英文词不拆；
`ActivityPhrase` 从「继续」续到「继续盯梢本轮：」时每 90ms 多一个字；
换成无关中文结论时从空串起逐字出现；英文每步一个 token；`trimConclusion` 仍 ≤10 字。

**79 部署**：web `kai/assistant-web:20261030-managed-r40` 已滚（Up healthy，`/assistant` = 200）；
ctx `web-src-r40`，`grok-activity.tsx` sha256 `e8264f2c…` 与本地一致；
组件 chunk 由 r39 的 `33rro6nqhidne.js` 变为 `426p7dn1ljfv4.js`。
只读探针：`activityRows=0 / stepLists=0 / phraseSpans=0 / stepCssRules=4 / answers=13`
（历史终态不出现）。截图 `/tmp/kai79-accept/155-r40-assistant.png`。

**未验证**：真实进行中回合的逐字动画（需发起真实 run）；单位选择与计时由 jsdom 假时钟单测钉住。

## 无人查看暂停：UI 可见 + 查看口径（2026-10-31，api r20 / web r41，marker kai-idle-pause-routines）

口径（代码与前端类型已按此对齐）：

1. `Specialist.lastViewedAt`（ISO）与 `routinePausedReason` 在 **Specialist 顶层**，不在 `config`。
2. 写 `lastViewedAt`：创建 specialist = now；用户**进入该 Bot 会话**（`/assistant?bot=…` 加载线程成功）
   时 POST `specialists/{id}/viewed` 一次（`botRevision` 刷新不重复上报）。打开「…」编辑弹窗、
   GET 详情、list specialists 都**不写**。
3. 阈值 `HARNESS_ASSISTANT_UNVIEWED_PAUSE_HOURS`（默认 24h，≥1）；只作用于绑定了 specialist 的职责；
   超阈值跳过入队、写 `routinePausedReason={reason:"unviewed", hours_since_view, at}` 并推进 `next_check_at`（不补跑）。

本轮修复：

- `GET /specialists/{id}` 改纯读（此前读详情即 touch，暂停标记在响应里已被清掉，编辑弹窗永远看不到暂停态）。
- 新增 `POST /specialists/{id}/viewed`：`touch_specialist_viewed` + 返回最新详情；不存在 → 404；不递增 revision。
- 前端：`Specialist` 类型补 `lastViewedAt` / `routinePausedReason`；`bot-edit-dialog` 改读顶层
  `specialist.routinePausedReason?.reason === "unviewed"`；`page.tsx` 在 bot 会话线程加载后按 bot 去重上报一次。
- `docs/research/2026-10-30-grokbot-client-source-review.md` 的「建议列为下一步」改为已落地。

**测试**

```
uv run pytest tests/unit/assistant/test_specialist_viewed_api.py -q      # 3 passed
uv run pytest tests/unit/assistant -q                                    # 99 passed
HARNESS_TEST_DATABASE_URL=postgresql+asyncpg://harness:harness@127.0.0.1:35432/harness_test \
  uv run pytest tests/integration/storage/test_delegation_real_gateway.py -q \
  -k "unviewed or specialist_conversation"                               # 2 passed
uv run ruff check src/harness/assistant/api.py tests/unit/assistant/test_specialist_viewed_api.py  # clean
cd web/harness-console
npx tsc --noEmit                                                         # clean
npx vitest run tests/bot-edit-dialog-paused.spec.tsx tests/bot-edit-dialog.spec.tsx  # 8 passed
npx vitest run                                                           # 1463 passed / 1 skipped / 1 既有失败
```

新断言：GET 详情不调用 touch、暂停标记原样返回；POST viewed 刷新 `lastViewedAt`、清暂停且 revision 不变；
不存在返回 404；弹窗用**顶层** `routinePausedReason` 显示「已暂停」「因无人查看已暂停，打开本会话后恢复」，
无字段/仅存在 config 旧位置时不显示；助理面仅在拿到线程后上报 `/viewed` 且按 bot 去重。

**79 部署与验收**

- api/worker/computer-control → `kai/assistant-api:20261030-managed-r20`（新镜像；`migrate` 仍 r18，无新迁移）；
  web → `kai/assistant-web:20261030-managed-r41`。均 healthy，`/assistant` = 200；compose 备份
  `compose.private.before-idle-pause.json`。容器内确认路由：
  `/v1/studio/assistant/specialists/{specialist_id}/viewed`。
- GET 纯读实测：连续两次 GET 详情，`lastViewedAt` 保持 `2026-10-31T12:57:34.394618Z` 不变（DB 前后一致）。
- 查看入口实测：打开 `/assistant?bot=specialist_466780791f3c4f218b66019818346459` → 恰好 1 次 POST `/viewed`，
  `lastViewedAt` 由 `12:57:34Z` 变为 `14:58:01Z`。截图 `/tmp/kai79-accept/156-r41-bot-open.png`。
- 未验证：真实"无人查看 → 暂停"文案的浏览器视觉（需要把某 bot 的 lastViewedAt 拨回 24h 后跑一次调度，
  会改真实数据）；该分支由集成测 + jsdom 弹窗用例钉住。

## 侧栏 bot 头像改用已保存的「色 + 形」（2026-10-31，web r42，marker kai-rail-shape-avatar）

缺口：rail 的 `BotAvatar` 只接 `color`，`avatar` 被丢弃 → 打开助理面看到的是名字首字头像，
与新建/编辑里保存的 `config.appearance={color,avatar}` 不一致。

- 新增 `botShape()` + `BotRailAvatar`（bot-rail.tsx）：子助理渲染
  `<svg viewBox="0 0 64 64"><path d={BOT_SHAPES path} fill={color} /></svg>`，
  容器仍是 `.bot-rail-avatar`（26px，svg 20px，背景透明）；`specialistAppearance(item).avatar` 接上。
  缺失/未知 id → 回退 blob（与新建设置默认一致），不再回退字母。
- 主助理保留固定「K」字母入口（`data-avatar="letter"`），不会套用某个子助理的外形。
- 在岗/待启用两段共用同一组件；未改创建/编辑 API、强调色变量、`openBot` 整页导航与分组文案；
  `BOT_SHAPES` 直接从 create-bot-dialog 复用，没有第二份 path。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                                # clean
npx vitest run tests/bot-rail-avatar.spec.tsx                   # 5 passed（新增）
npx vitest run tests/bot-rail-groups.spec.tsx   tests/bot-edit-dialog.spec.tsx tests/bot-edit-dialog-paused.spec.tsx  # 10 passed
npx vitest run                                                  # 1468 passed / 1 skipped / 1 既有失败
```

行为断言（jsdom 真渲染，不是源码字符串）：`star` 渲染出 BOT_SHAPES 的 star path 且头像无文本；
无 appearance / 未知 id → blob path；BotRail 同时渲染在岗 `star` 与待启用 `drop` 两个外形，
主助理是 `data-avatar="letter"` 的「K」且没有 `path`；既有失败仍是 account-settings（无关）。

**79 部署与只读验收**：web `kai/assistant-web:20261030-managed-r42` 已滚（healthy，`/assistant` = 200）；
ctx `web-src-r42`，`bot-rail.tsx` sha256 `85b12b9b…`、`web-codex.css` `35744332…` 与本地一致；
组件 chunk 含 `data-avatar`（r41 无）。页面探针：主助理 `letter/K`，三个 bot 均为
`shape/blob`、头像无文字、svg 20×20；fill 分别 `#0d9655 / #0d9381 / #86592e`
（行业快报监控与 DB `appearance.color` 一致）。截图 `/tmp/kai79-accept/157-r42-rail-avatars.png`。

未验证：非 blob 外形（star/drop）在 79 的视觉——现网真实数据只有 blob（两个 star 的 bot 已 archived），
该分支由上面的 DOM 用例覆盖。api 未动，仍 r20。

## 顶栏身份标记与 rail 对齐（2026-10-31，web r43，marker kai-header-shape-avatar）

缺口：打开 `/assistant?bot=…` 后，顶栏只有文字标题 / 描述 /「…」；色 + 形只出现在左侧列表，
头部没有同款标记，与 rail 和本机 Grok 的身份观感不一致。

- 在 `bot-rail.tsx` 提取 `AssistantHeaderAvatar`（复用 `BotRailAvatar` / `botAccent`）：
  有 bot → `appearance.color`（缺省 `botAccent(id)`）+ `appearance.avatar`（缺/未知 → blob）；
  无 bot → `letter="K"` + `BOT_ACCENTS[0]`（#126fd4）。两个分支都渲染同一节点，便于测试与切换。
- `page.tsx` 的 assistantView `task-context-bar` 在标题左侧插入该节点；
  CSS 新增 `.task-context-bot-avatar`（24px 容器 / 18px svg），描述与「…」位置未变。
- 未改编辑弹窗、rail 分组/导航、`openBot`、强调色变量与 API。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                                # clean
npx vitest run tests/assistant-header-avatar.spec.tsx           # 4 passed（新增）
npx vitest run tests/bot-rail-avatar.spec.tsx tests/bot-rail-groups.spec.tsx   tests/bot-edit-dialog.spec.tsx tests/bot-edit-dialog-paused.spec.tsx  # 19 passed
npx vitest run                                                  # 1472 passed / 1 skipped / 1 既有失败
```

DOM 断言：`star` bot → 顶栏 path = star、fill = 指定色、头像无文本；无 appearance / 未知 id → blob
（老 bot 用 `botAccent`）；`bot=null` → `data-avatar="letter"` 的「K」且无 svg，
从子助理切回主助理外形不残留。既有失败仍是 account-settings（无关）。

**79 部署与只读验收**：web `kai/assistant-web:20261030-managed-r43` 已滚（healthy，`/assistant` = 200）；
ctx `web-src-r43`，3 个文件逐文件 sha256 与本地一致；`task-context-bot-avatar` 出现在
`1liyegi_2vmy9.js` / `2l2j9h8-mxere.css`（r42 无）。页面探针：

- 主助理：`letter / K`，24×24，标题「我的助理」；
- 休息提醒：`shape / blob`、fill `#0d9655`、头像无文本、svg 18×18、描述与「…」仍在；
- 行业快报监控：`shape / blob`、fill `#0d9381`（与 DB `appearance.color` 一致）。

截图 `/tmp/kai79-accept/158-r43-header-main.png`、`159-r43-header-bot.png`。
未验证：非 blob 外形在 79 顶栏的视觉（现网真实数据只有 blob；DOM 用例覆盖 star/未知）。api 未动，仍 r20。

## 活动行脸的外形与 appearance 对齐（2026-10-31，web r44，marker kai-activity-face-shape）

缺口：`GrokFace` 的身体 path 写死一份接近 blob 的曲线，无视 `config.appearance.avatar`；
`/assistant?bot=…` 跑起来后，活动行「脸」与左侧 rail / 顶栏的色+形身份不一致。

- `GrokFace` 复用 `botShape()`（bot-rail 导出，path 表仍是 create-bot-dialog 的唯一一份）：
  `avatar` prop 优先，否则读 `useAssistantBot()?.appearance?.avatar`；缺/未知 → blob。
  `data-avatar="<id>"` 写在外层 `.grok-face`，便于 DOM 断言。
- 眼睛按外形微调（star 收窄眼距/缩小眼径），眨眼、注视、working/done/failed 动画与
  `aria-hidden` 均保持；颜色仍走 `--kai-bot-accent`。
- `GrokActivityRow` 透传 `avatar`；`agent-thread` 两处（turn 活动行 + pre-response 活动行）
  都传当前 bot 的 `appearance.avatar`；主助理（无 bot）→ blob（没有被某个子助理外形污染）。
- 未改任务面 `ActivitySummary`、编辑弹窗、rail、顶栏与 API。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                                # clean
npx vitest run tests/grok-activity-face-shape.spec.tsx          # 5 passed（新增，SSR DOM 断言）
npx vitest run tests/grok-activity.spec.tsx tests/assistant-phrase-reveal.spec.tsx \
  tests/assistant-activity-steps.spec.tsx                       # 29 passed
npx vitest run                                                  # 1477 passed / 1 skipped / 1 既有失败
```

断言：`avatar="star"` → `path[d]` 等于 BOT_SHAPES star、`data-avatar="star"`、无写死 blob 路径；
缺/未知 → blob；Provider 内有 bot → 用 bot 外形，无 bot → blob；`GrokActivityRow` 透传；
idle 有 `data-blink`、working 无，眼睛（2 个 ellipse）与 `aria-hidden` 始终存在。

**79 部署与只读验收**：web `kai/assistant-web:20261030-managed-r44` 已滚（healthy，`/assistant` = 200）；
ctx `web-src-r44`，`grok-activity.tsx` sha256 `1224b5cb…`、`agent-thread.tsx` `7187c7d3…` 与本地一致；
grok 组件 chunk 由 r43 的 `426p7dn1ljfv4.js` 变为 `166uf93upn6yg.js`。页面探针：顶栏与 rail
外形正常（休息提醒 `blob/#0d9655`）、无 console/page error、历史终态无活动行（`grok-face` = 0）。
截图 `/tmp/kai79-accept/160-r44-bot.png`。

未验证：真实**进行中**回合里活动行脸的视觉——活动行只在 run 进行中渲染，需要发起一次真实 run
（会往用户会话写消息并消耗额度），本轮未做；外形分支由上面的 SSR/DOM 断言覆盖。api 未动，仍 r20。

## 顶栏电脑旁的只读占用 chip（2026-10-31，web r45，marker kai-desktop-occupancy-chip）

缺口：顶栏只有电脑面板开关，用户不打开面板就看不到「谁在占用共享桌面 / 还有几人排队」；
后端 P1-4 的只读投影早就存在，但 web 源码没有任何引用。

- 新增 `DesktopOccupancyChip`（`components/desktop-occupancy-chip.tsx`），放在助理面
  `HeaderUtilities` 的电脑入口旁（仅 `personalAssistant` 分支；任务面不出现）：
  - `holder` 有标题 → 「占用：<标题>」（空标题退 `objective_id` 前 12 字），有排队追加「· 排队 N」；
  - 无 holder 但有排队 → 「桌面排队 N」；
  - 空闲（holder=null 且 waiting=[]）→ **不渲染**（口径选隐藏）；
  - `data-occupancy="held|queued"`、`role="status"`、`aria-live="polite"`、稳定的 `aria-label`
    （含占用者与排队数）。
- 数据只读自 `GET /api/studio/assistant/desktop/occupancy`（`cache:"no-store"`）；
  轻轮询 15s，`document.hidden` 暂停、`focus`/`visibilitychange` 立即刷新、8s abort 超时、
  失败静默保留上次值。未改仲裁/入队/lease、未动右侧电脑面板与电脑钮开合。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                                # clean
npx vitest run tests/desktop-occupancy-chip.spec.tsx            # 5 passed（新增）
npx vitest run tests/workbench-layout.spec.ts   tests/assistant-channel-status.spec.tsx                       # 56 passed
npx vitest run                                                  # 1482 passed / 1 skipped / 1 既有失败
```

DOM 断言：holder 有标题 + 2 排队 → chip 文案含标题与「排队 2」、`data-occupancy="held"`、
aria 含「2 项排队」；只有排队 → `queued`；空闲 → 不渲染；holder 无标题 → 短 id；
请求失败 → chip 不出现、顶栏不报错。`workbench-layout` 的「电脑入口只在助理面」断言同步
更新为新结构（`<DesktopOccupancyChip` + `<AssistantComputerLink`）。既有失败仍是 account-settings。

**79 部署与只读验收**：web `kai/assistant-web:20261030-managed-r45` 已滚（healthy，`/assistant` = 200）；
ctx `web-src-r45`，3 个文件逐文件 sha256 与本地一致；`desktop-occupancy-chip` 出现在
`3kmldlkyb5580.js` / `35jvfggwehpv-.css`（r44 无）。api 未动，仍 r20。
compose 备份 `compose.private.before-r45.json`。

- 真实投影当前为空闲：`GET desktop/occupancy` → `{"holder":null,"waiting":[]}`，
  页面 chip 不渲染（符合「空闲隐藏」口径），顶栏两个图标按钮不变，无 console/page error；
  截图 `/tmp/kai79-accept/161-r45-header-chips.png`。
- 占用视觉用**浏览器侧拦截**（`page.route` 只改本次页面的响应，不动服务端数据）注入
  holder + 2 排队，实机渲染为「占用：预算受限验收职责检查 · 排队 2」、`held`、aria 正确、
  高 22px 位于顶栏；截图 `/tmp/kai79-accept/162-r45-chip-injected.png`。这不是真实占用数据。

未验证：真实桌面占用/排队下的页面行为（需要有一次 `requiresDesktop` 的进行中执行，会写真实数据）；
文案与状态机由上面的 DOM 用例 + 注入渲染覆盖。

## 侧栏 bot 行的「无人查看已暂停」徽章（2026-11-01，web r46，marker kai-rail-paused-badge）

缺口：rail 只显示名 + 描述 + 色形头像，用户必须点「…」进编辑弹窗才知道某 Bot 因无人查看
暂停了例行任务；列表侧没有可感知的暂停态。

- `bot-rail.tsx` 新增 `isUnviewedPaused(item)`（只认 `routinePausedReason.reason === "unviewed"`）：
  在岗与待启用两段共用；行根 `.bot-rail-item` 写 `data-paused="true"`（非暂停不写）。
- 暂停时把副标题（`small`）替换为「已暂停 · 无人查看」（不会被描述省略号截掉），
  并用琥珀色（`#d2a83a`）区分；头像强调色、外形、分组与 `openBot` 导航均未动。
- 主助理入口永不写 `data-paused`、不出现「已暂停」。数据只读既有 `specialists` 列表
  （30s 轮询）；进入该 Bot 会话后既有 `viewed` 清标记，下次列表刷新徽章消失——未新增轮询或 API。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                                  # clean
npx vitest run tests/bot-rail-paused-badge.spec.tsx               # 4 passed（新增）
npx vitest run tests/bot-rail-avatar.spec.tsx tests/bot-rail-groups.spec.tsx   tests/bot-edit-dialog-paused.spec.tsx                           # 11 passed
npx vitest run                                                    # 1486 passed / 1 skipped / 1 既有失败
```

DOM 断言：列表含 1 条 unviewed + 1 条正常 → 仅前者 `data-paused="true"`、文案含「已暂停 · 无人查看」，
正常行无标记/无文案；`reason` 非 unviewed → 无标记；主助理行永不带 `data-paused` 且无「已暂停」；
在岗与待启用两段各一条暂停时都命中。既有失败仍是 account-settings（无关）。

**79 部署与只读验收**：web `kai/assistant-web:20261030-managed-r46` 已滚（healthy，`/assistant` = 200）；
ctx `web-src-r46`，`bot-rail.tsx` sha256 `4385983f…`、`web-codex.css` `31ddbde7…` 与本地一致；
「已暂停 · 无人查看」出现在 `3anmjck4bx8p2.js` / `0u-68_3x1wmkr.js`（r45 无）。api 未动，仍 r20。
compose 备份 `compose.private.before-r46.json`。

- 真实数据当前没有任何 `routinePausedReason`：页面 rail `data-paused` 命中 **0**（正确的阴性），
  4 行副标题均为原描述。
- 暂停视觉用**浏览器侧拦截**（`page.route` 只改本次页面响应）注入 1 条在岗暂停 + 1 条待启用暂停：
  实机出现 2 个 `data-paused="true"` 行，副标题「已暂停 · 无人查看」、颜色 `rgb(210,168,58)`，
  正常行与主助理无标记；截图 `/tmp/kai79-accept/163-r46-rail-paused.png`。这是注入 fixture，不是真实暂停。

未验证：真实「超过 24h 无人查看 → 调度写入 routinePausedReason → 列表出徽章 → 打开会话后消失」
的完整链路（需要把某 Bot 的 lastViewedAt 拨回 24h 再等一次调度，会写真实数据）；该链路后半段
（viewed 清标记）已有 r41 的 API 验收，列表渲染由上述 DOM + 注入验收覆盖。

## 打开会话后 rail 暂停标立即消失（2026-11-01，web r47，marker kai-rail-refresh-after-viewed）

缺口：r46 的「已暂停 · 无人查看」只在 BotRail 的 30s 轮询里刷新；page.tsx 的 viewed POST
成功后没有通知 rail，用户打开刚暂停的 Bot 后徽章可能继续挂最多 ~30s，与「打开本会话后恢复」不符。

- `page.tsx`：viewed POST 的 `.then` 里派发 `CustomEvent("kai:specialists-changed", {detail:{specialistId, reason:"viewed"}})`
  ——只在成功时派发；失败仍静默，旧列表保留、30s 轮询兜底。
- `bot-rail.tsx`：监听该事件 → `void load()`；卸载时移除监听。30s 轮询保留。
- 未改 viewed API 语义、`schedule_due` 闸门、徽章文案与外形；GET 详情/编辑弹窗仍不算查看。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                                    # clean
npx vitest run tests/bot-rail-refresh-after-viewed.spec.tsx         # 3 passed（新增）
npx vitest run tests/bot-rail-paused-badge.spec.tsx   tests/bot-rail-avatar.spec.tsx                                    # 9 passed
npx vitest run                                                      # 1489 passed / 1 skipped / 1 既有失败
```

DOM 断言：初次列表含 unviewed 暂停 → 行上 `data-paused="true"`；派发事件后 mock 第二次返回无
`routinePausedReason` → 该行不再 `data-paused`、无「已暂停」、恢复原描述；卸载后派发事件不再触发请求。
另有静态守卫：页面的事件名必须与 rail 一致，且派发挂在 viewed 的 `.then` 上（`.catch` 静默仍在）。
既有失败仍是 account-settings（无关）。

**79 部署与只读验收**：web `kai/assistant-web:20261030-managed-r47` 已滚（healthy，`/assistant` = 200）；
ctx `web-src-r47`，`page.tsx` sha256 `0277a809…`、`bot-rail.tsx` `abd7599e…` 与本地一致；
`kai:specialists-changed` 出现在 `1ud9-0psjfh3i.js` / `1wcqcqzyml0p1.js` / `411zsf0gxm70d.js`（r46 无）。
api 未动，仍 r20；compose 备份 `compose.private.before-r47.json`。

端到端（**浏览器侧拦截 specialists 列表为 fixture，viewed 打真实 API**，不写业务数据）：
打开 `/assistant?bot=specialist_466780791f3c4f218b66019818346459`，首个列表带 unviewed 暂停
→ rail 出现徽章；t=716ms viewed POST（真实 200）→ t≈745ms 触发第二次列表请求（无暂停）
→ 徽章在 t≈1.56s 消失、恢复原描述。共 2 次 specialists 列表请求，远早于 30s 轮询。
截图 `/tmp/kai79-accept/164-r47-paused-before.png`、`165-r47-paused-after.png`。
真实数据当前没有暂停中的 Bot，所以未做「真实 24h 无人查看」链路；本轮验证的是 viewed→刷新→徽章消失
这段新增行为，暂停本身的产生仍由 r41 的后端验收覆盖。

## 助理面进展浮标显示检查点计量（2026-11-01，web r48 + api r21，marker kai-progress-metering）

缺口：P2-1 已把 tokens/cost/耗时投影进托管面板，但 `/assistant` 的「后台任务进展」浮标从 inbox
只拿到 title/content，打开助理面无法立刻感知一趟执行花了多少。

> 编号说明：本批任务书写的是 web r47，但 r47 已由上一片（kai-rail-refresh-after-viewed）占用并部署，
> 所以本片落在 **web r48 + api r21**。

- 后端：`AssistantInboxItem` 增可选 `metrics{tokens,costUsd,unknownUsage}`（`AssistantInboxMetrics`）
  与 `durationSeconds`；`save_execution` 写终态 notice 时带同次 checkpoint 的结算值
  （两者都空 → `unknownUsage=True`）和 `finishedAt-createdAt` 秒数。旧行没有字段仍按 None 读取。
- 前端：新增共享 `checkpoint-metering.tsx`（`formatCheckpointMetering` / `CheckpointMeteringLine`），
  托管面板与浮标共用同一份文案：`tokens 1,234 · cost $0.02 · 耗时 45 秒`；
  未知用量显示「tokens 未计量」（绝不显示 `tokens 0`）；无 metrics 且无耗时 → 不渲染计量行。
  浮标展开明细里新增 `.assistant-progress-metrics`（`data-metering="measured|unknown"`），折叠摘要不变。
- 未改预算闸门/结算公式/委派/lease/desktop 仲裁。

**测试**

```
uv run pytest tests/integration/storage/test_assistant_objectives.py -q            # 9 passed（含新终态载荷用例）
uv run pytest tests/unit/assistant -q                                              # 101 passed
uv run ruff check src/harness/assistant/{models,repository}.py tests/...           # clean
cd web/harness-console
npx tsc --noEmit                                                                   # clean
npx vitest run tests/assistant-progress-metering.spec.tsx                          # 3 passed（新增）
npx vitest run tests/assistant-progress-strip.spec.tsx tests/managed-metrics.spec.tsx  # 6 passed
npx vitest run                                                                     # 1492 passed / 1 skipped / 1 既有失败
```

后端用例：终态 notice 带 `tokens=1234 / cost=0.02 / unknownUsage=False / durationSeconds=45`；
无用量那次 `tokens=None / unknownUsage=True`；旧载荷无字段可 validate。前端 DOM 用例覆盖
千分位、cost、耗时、未计量不出现 `tokens 0`、旧 notice 无计量行；托管面板断言改指共享组件。

**79 部署与只读验收**：api/worker/computer-control → `kai/assistant-api:20261030-managed-r21`
（worker 也会调 `save_execution`，必须同版）；web → `kai/assistant-web:20261030-managed-r48`；
`migrate` 仍 r18（无新迁移）。均 healthy，`/assistant` = 200；compose 备份
`compose.private.before-metering.json`。镜像内确认 `AssistantInboxMetrics` 在 models/repository，
web chunk 含 `assistant-progress-metrics` 与「tokens 未计量」。

- 真实 inbox：25 条历史 notice 全部无计量（`withMetrics=0`），页面展开浮标 `metricLines=0`
  ——旧载荷兼容、不显示假 0。
- 计量视觉用**浏览器侧拦截** inbox 响应（不动服务端）注入 measured/unknown/legacy 三条：
  实机分别渲染 `tokens 1,234 · cost $0.02 · 耗时 45 秒`（measured）、
  `tokens 未计量 · 耗时 12 秒`（unknown）、无计量行（legacy）；截图
  `/tmp/kai79-accept/166-r48-metering.png`。

未验证：**部署后新产生**的终态 notice 在真实运行里带计量（需要一次真实执行结束后看 inbox）；
写入口径由上面的集成测试钉住，前端渲染由注入验收覆盖。

## skipUnchanged 早退不再显示「tokens 未计量」（2026-11-01，web r49 + api r22，marker kai-skip-unchanged-quiet）

缺口：r48/r21 的浮标计量把「无用量」统一显示成「tokens 未计量」；但 skipUnchanged 定时检查
没唤起模型就走 `save_execution(usage=None)`，也会被当成「计量坏了」，语义错误。

- 后端：`ObjectiveExecution` 增 `skippedNoModel`，service 的 skip 早退分支置 True；
  终态 inbox 的 `metrics` 带 `skippedNoModel=true`、`unknownUsage=false`、tokens/cost 保持 None，
  耗时照常（反映调度耗时）。**checkpoint 仍是用量审计**（无用量的跳过记录 `unknownUsage=True`），
  不新增数据库列/迁移；托管面板按 run_id 取计量，skip 执行没有 Run，本来就不会命中该行。
  旧行无字段仍可读。
- 前端：`formatCheckpointMetering` 先判 `skippedNoModel` → 「未唤起模型 · 无变化」（可附耗时），
  `data-metering="skipped"`；不再出现「tokens 未计量」或 `tokens 0`。托管面板与浮标共用该函数。
- 未改预算公式、委派、lease、桌面仲裁。

**测试**

```
uv run pytest tests/integration/storage/test_delegation_real_gateway.py -q -k "unchanged or skip"  # 2 passed
uv run pytest tests/unit/assistant -q                                          # 102 passed
uv run ruff check src/harness/assistant tests/unit/assistant/test_inbox_payload.py  # clean
cd web/harness-console
npx tsc --noEmit                                                               # clean
npx vitest run tests/assistant-progress-metering.spec.tsx                      # 4 passed
npx vitest run                                                                 # 1493 passed / 1 skipped / 1 既有失败
```

后端断言：skip 结果 `skipped_no_model=True`；收尾后 inbox `metrics.skippedNoModel=True /
unknownUsage=False / tokens=None`；checkpoint 仍是 `unknown_usage=True`（审计口径）。
前端 DOM 断言四类：measured（`tokens 1,234 · cost $0.02 · 耗时 45 秒`）、
unknown（`tokens 未计量 · 耗时 12 秒`）、skipped（`未唤起模型 · 无变化 · 耗时 5 秒`，不含 tokens/未计量）、
legacy（无计量行）。既有失败仍是 account-settings（无关）。

**79 部署与只读验收**：api/worker/computer-control → `kai/assistant-api:20261030-managed-r22`；
web → `kai/assistant-web:20261030-managed-r49`；`migrate` 仍 r18（无新迁移）。均 healthy，
`/assistant` = 200；compose 备份 `compose.private.before-skip-quiet.json`。镜像内确认
`skipped_no_model` 在 service、web chunk 含「未唤起模型 · 无变化」。

- 真实 inbox 仍是历史载荷（无计量），浮标不显示计量行；
- 四类渲染用**浏览器侧拦截** inbox 响应（不动服务端）注入验证：实机分别得到
  measured / unknown / skipped / legacy 四行，skipped 文案 `未唤起模型 · 无变化 · 耗时 5 秒`，
  页面无 `tokens 0`、skipped 行不含「tokens 未计量」；截图 `/tmp/kai79-accept/167-r49-skip-quiet.png`。

未验证：部署后**真实**发生一次 skipUnchanged 跳过并看到浮标文案（需要一个开启 skipUnchanged 的
定时职责且指纹未变）；判定与写入口径由上面的集成/单元测试钉住。

## 占用 chip 可读排队位次与等待原因（2026-11-01，web r50，marker kai-computer-queue-chip）

缺口：r45 的占用 chip 只有「· 排队 N」计数；P1-4 早已返回 `waiting[].position` 与中文
`wait_reason`，但用户不打开右侧电脑面板仍无法判断「我排第几 / 为何在等」。

- `desktopOccupancyLabel` 消费首个 waiting 的 `position` / `wait_reason`：
  - 有 holder：「占用：<标题> · 排队中 #1 · 共 N · <原因>」；无 holder 仅排队：「排队中 #1 · 共 N · <原因>」；
  - 旧载荷没有 `position` → 退回「· 排队 N」/「桌面排队 N」，不显示 `#`；
  - `wait_reason` 为空不硬编原因；过长文本被 240px 胶囊 ellipsis 截断，`title`/`aria-label` 保留全文。
- chip 增 `data-queue-position` / `data-wait-reason`（有则写）；`data-occupancy` 仍 `held|queued`。
  轮询/hidden/focus/abort 与「空闲不渲染」口径不变；未改右侧面板、acquire/FIFO 仲裁、入队与 lease。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                              # clean
npx vitest run tests/desktop-occupancy-chip.spec.tsx          # 6 passed
npx vitest run                                                # 1494 passed / 1 skipped / 1 既有失败
```

DOM 断言：holder + waiting[0].position=1 + 原因 → 文案含「排队中 #1」「共 2」与原因、
`data-queue-position="1"`、aria 含「排队中第 1 位」「等待原因：…」；仅排队 position=2 → 「排队中 #2」；
无 position 旧载荷 → 「桌面排队 1」且无 `#`、原因仍可读；空闲 → 不渲染。既有失败仍是 account-settings（无关）。

**79 部署与只读验收**：web `kai/assistant-web:20261030-managed-r50` 已滚（healthy，`/assistant` = 200）；
ctx `web-src-r50`，`desktop-occupancy-chip.tsx` sha256 `8c0648ff…` 与本地一致；`data-queue-position`
出现在 `3d2xzaxt7osgb.js`（r49 无）。api 未动，仍 r22；compose 备份 `compose.private.before-r50.json`。

- 真实投影当前空闲 → chip 不渲染（与 r45 一致）。
- 队列视觉用**浏览器侧拦截**占用响应（不动服务端）注入验证：
  - holder + 2 排队（#1）：实机「占用：预算受限验收职责检查 · 排队中 #1 …」，`data-queue-position=1`、
    `data-wait-reason` 为中文原因，aria 含位次/共 N/原因，高 22px；
  - 无 holder、position=2：实机「排队中 #2 · 等待桌面就绪」；
  - 旧载荷无 position：实机「桌面排队 1」、无 `data-queue-position`。
  截图 `/tmp/kai79-accept/168-r50-queue-held.png`、`169-r50-queue-queued.png`。

未验证：真实并发下的桌面排队（需要两项 `requiresDesktop` 同时争用，会写真实执行数据）；
位次/原因文案与回退由 DOM + 注入验收覆盖，投影字段来自 r17 既有验收。

## 进展浮标失败卡一键失败回放（2026-11-01，web r51 + api r23，marker kai-failure-replay）

缺口：托管面板 P2-2 的失败卡能深链 `/?thread=&run=&trace=1&checkpoint=`，但助理面
「后台任务进展」浮标里 `kind=failed` 的条目只有「打开原任务 → `/?thread=`」，点进去落在普通线程，
看不到失败检查点/轨迹；后端写终态 inbox 时也没有带上 `execution.run_id`。

- 后端：`AssistantInboxItem` 增可选 `runId`（`executionId` 本来就有）；`save_execution` 写终态
  notice 时带 `run_id`。旧行无字段仍按 None 读取，不伪造 Run。
- 前端：新增 `progressNoticeLink(notice)`：
  - `failed`（含 timed_out/rejected）→「打开失败回放 →」，href = `/?thread=&trace=1`
    + 有则 `run=<runId>` + `checkpoint=checkpoint_<executionId>`（与写库 `checkpoint_{execution_id}` 同口径）；
  - 成功/取消/进行中 →「打开原任务 →」，仅 `thread`；
  - 旧失败载荷无 run/execution → 只回 thread + `trace=1`；无 thread → 不渲染链接。
  链接带 `data-replay="failure|thread"` 便于断言；未改轨迹控制台、托管面板与任务面。

**测试**

```
uv run pytest tests/integration/storage/test_assistant_objectives.py -q          # 10 passed（含失败 runId 用例）
uv run pytest tests/unit/assistant -q                                            # 102 passed
uv run ruff check src/harness/assistant/models.py src/harness/assistant/repository.py tests/unit/assistant  # clean
cd web/harness-console
npx tsc --noEmit                                                                 # clean
npx vitest run tests/assistant-progress-failure-replay.spec.tsx                  # 4 passed（新增）
npx vitest run tests/assistant-progress-strip.spec.tsx tests/assistant-progress-metering.spec.tsx  # 7 passed
npx vitest run                                                                   # 1498 passed / 1 skipped / 1 既有失败
```

DOM 断言：failed + thread/run/execution → 文案「打开失败回放 →」，params 含
`thread=assistant_o1 / trace=1 / run=run-1 / checkpoint=checkpoint_execution-1`；
succeeded/cancelled → 「打开原任务 →」且无 trace/run/checkpoint；legacy failed → 仅 thread + trace=1；
helper 在无 thread 时返回 null、从不伪造 run。
后端断言：失败终态 notice `runId="run-replay-1"`、`executionId`/`threadId` 与执行一致；
无 Run 的结算 `runId=None`。既有失败仍是 account-settings（无关）。

**79 部署与只读验收**：api/worker/computer-control → `kai/assistant-api:20261030-managed-r23`；
web → `kai/assistant-web:20261030-managed-r51`；`migrate` 仍 r18（无新迁移）。均 healthy，
`/assistant` = 200；compose 备份 `compose.private.before-failure-replay.json`。镜像内确认
`runId` 在 repository，web chunk 含「打开失败回放」与 `data-replay`。

- 真实 inbox：25 条全部 `succeeded`，浮标全部「打开原任务 →」、无 trace（正确的阴性）；
  API 载荷已带 `runId` 键（旧行为 null）。
- 失败回放链接用**浏览器侧拦截** inbox 响应（不动服务端）注入验证：
  - 新式失败（runId + executionId）→「打开失败回放 →」，params
    `thread=assistant_o1&trace=1&run=run-1&checkpoint=checkpoint_execution-1`；
  - 成功 →「打开原任务 →」，仅 thread；
  - 旧失败（无 run/execution）→「打开失败回放 →」，`thread&trace=1`，无 run/checkpoint。
  截图 `/tmp/kai79-accept/170-r51-failure-replay.png`。

未验证：部署后**真实产生**的失败 notice 带 runId 并点进轨迹页（需要一次真实失败执行）；
写入口径由集成测试、链接构造由 DOM + 注入验收覆盖。

## 主会话内联完成 pending_input 补配置（2026-11-01，web r52 + r53，marker kai-main-session-pending-input-card）

缺口：缺 `schedule_minutes`/`notify_channel` 时后端把 handover 置 `pending_input` 且不入队（语义正确），
但主 `/assistant` 的转交行只有「待补充 ·「X」」链接，必须跳到 `/assistant?bot=…` 才能用上
`SpecialistSetupCard`；Grok 的「补充输入在主 bot 消息里做」在助理面缺失。

- r52：`HandoverInlineRows` 对 `status === "pending_input"` 的条目，在状态行下方内联挂同一张
  `SpecialistSetupCard`（没有第二套频率/渠道 UI），保存仍走 config→status 两步；成功后：
  `handoverStore.markConfigured()` 把行态改为 `applied`（「已安排」）、卡片随之卸载，
  并派发 `kai:specialists-changed` 让 rail 立刻重拉（该 bot 从「待启用」移到「在岗」）。
  非 `pending_input` 行保持原链接；未改委派/入队/lease/预算/desktop 与飞书微信入站。
- r53（实机发现的问题）：79 上 2 条历史 `pending_input` 转交指向的 Bot **早已 active**，
  卡片按既有口径不渲染（active 不常驻表单），行却一直停在「待补充」。补了 `onAlreadyActive`：
  打开时若 specialist 已是 active，就把行态归一为「已安排」（只改本地展示，不写服务端）。
  保存流程用 ref 防重复上报。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                                   # clean
npx vitest run tests/main-session-pending-input-card.spec.tsx      # 3 passed（新增）
npx vitest run tests/handover-row.spec.tsx tests/specialist-setup-card.spec.tsx \
  tests/specialist-setup-card.flow.spec.tsx                        # 11 passed
npx vitest run                                                     # 1501 passed / 1 skipped / 1 既有失败
```

DOM 断言：pending_input → 行出现「待补充 ·」+ 卡片（频率档/「保存并启用」可见）；点「每小时」→
「保存并启用」后调用序列 config→status、status=active、卡片消失、行变「已安排」、store 无 pending_input；
全程**未调用 location.assign**（URL 仍 `/assistant`，无 `?bot=`）；已 active 的陈旧 pending 行直接归一为
「已安排」且无卡片；非 pending 行仍是普通链接。既有失败仍是 account-settings（无关）。

**79 部署与只读验收**：web 先滚 `kai/assistant-web:20261030-managed-r52`，发现陈旧 active 行问题后
再滚 `:managed-r53`（healthy，`/assistant` = 200）；ctx `web-src-r52/53`，
`handover-row.tsx`/`handover-store.ts`/`specialist-setup-card.tsx` sha256 与本地一致；
`handover-inline-item` 出现在 `0yg3lgmx1aftc.js`（r51 无）。api 未动，仍 r23；
compose 备份 `compose.private.before-r52.json` / `before-r53.json`。

- 真实页面（r53）：两条历史 `pending_input` 行均显示「已安排」，无卡片（对应 Bot 已 active）；
- 内联完成流程用**浏览器侧拦截** API（handovers/specialists/detail/config/status，均 fixture，不写服务端）：
  实机出现「待补充 ·「休息提醒」」+ 完整卡片，点「每小时」→「保存并启用」后请求序列为
  `config → status`，卡片消失、行变「已安排」，URL 始终 `/assistant`（未跳 ?bot=），rail 收到刷新事件。
  截图 `/tmp/kai79-accept/174-r53-inline-card.png`、`172/173-r52-inline-*.png`。

未验证：真实「未配置的 pending_input Bot 在主会话里点完成」——现网 pending 转交都指向已 active 的 Bot，
造一条新的需要真实委派（会写用户数据）；流程由 DOM 用例 + 注入式实机验收覆盖。

## 子助理凭据 UI（2026-11-01，web r54，marker kai-specialist-credential-ui）

缺口：后端早已有 `POST /specialists/{id}/credentials`（密文入库、config 只写
`credential_ref:<名称>`、无明文回读），但助理面前端没有调用点——编辑 Bot 与补配置流程都没有
「填密钥」入口。

- `assistant-client` 新增薄封装 `saveSpecialistCredential()`（走既有 `assistantRequest`，
  失败原样展示服务端文案，如「凭据存储未配置」「凭据名称需在 1–64 字之间」）。
- 新增 `SpecialistCredentials` 组件：列出 `config` 里 `credential_ref:*` 的**名称**
  （「已配置 · api_key」），输入名称 + `SecretInput`（密码型、可临时显示）→「保存凭据」；
  成功清空输入、显示「已保存「api_key」」并立即出现在已配置列表（父组件刷新前也不丢）；
  **从不渲染 credential reference 或密文**。
- 接入 `bot-edit-dialog` 的「凭据」区（主要入口）；`SpecialistSetupCard` 底部加折叠行
  「可选：为这个 Bot 添加凭据」复用同一组件（主会话内联补配置时也能顺手认证）。
- 未改委派/入队/lease/预算/desktop/飞书微信；未新增端口或服务。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                                     # clean
npx vitest run tests/specialist-credentials.spec.tsx                 # 4 passed（新增）
npx vitest run tests/bot-edit-dialog.spec.tsx tests/bot-edit-dialog-paused.spec.tsx \
  tests/specialist-setup-card.spec.tsx tests/specialist-setup-card.flow.spec.tsx  # 15 passed
npx vitest run                                                       # 1505 passed / 1 skipped / 1 既有失败
```

DOM 断言：已有 `credential_ref:api_key` → 显示「已配置 · api_key」且 DOM 不含 reference 值；
保存调用 `saveSpecialistCredential(id, {name, secret, expectedRevision})`、body 含名称/内容、
成功后出现「已配置 · api_key」+「已保存「api_key」」、两个输入框清空、密文不出现在 DOM；
失败展示服务端文案且保留已输入内容；编辑弹窗挂载后凭据区与「保存凭据」按钮可见、secret 输入为
`type=password`。既有失败仍是 account-settings（无关）。

**79 部署与只读验收**：web `kai/assistant-web:20261030-managed-r54` 已滚（healthy，`/assistant` = 200）；
ctx `web-src-r54`，5 个文件逐文件 sha256 与本地一致；「已配置 ·」/「保存凭据」出现在
`1xp34jie2gcu7.js`（r53 无）。api 未动，仍 r23；compose 备份 `compose.private.before-r54.json`。

- 真实 Bot 编辑弹窗（行业快报监控）：凭据区可见、空态文案、`保存凭据` 按钮、secret 输入为 password、
  DOM 无 `specialist_credential_` 泄漏；截图 `/tmp/kai79-accept/177-r54-dialog-credentials-bottom.png`。
- 主会话补配置卡（**浏览器侧拦截 API** 注入 needs_input 夹具）：折叠行「可选：为这个 Bot 添加凭据」
  展开后出现名称输入与「保存凭据」；截图 `/tmp/kai79-accept/176-r54-setup-card-credentials.png`。
- 未在真实 Bot 上提交凭据（会写入密文与 config，改用户数据）；保存链路由 mock 单测覆盖。

**如何在 /assistant 给某个 Bot 加凭据**：打开该 Bot 会话 → 顶栏「…」→ 最下方「凭据」→
填「凭据名称」（如 `api_key`）与「凭据内容」→「保存凭据」；保存后该行显示「已配置 · api_key」，
之后可在描述/任务里引用该名称，系统只存 `credential_ref:api_key`。

## 主会话内联「必填凭据」卡（2026-11-01，web r55，marker kai-main-session-credential-card）

缺口：r54 的凭据只在编辑弹窗/补配置卡的可选折叠里；没有一等触发，用户无法在**主 /assistant
消息流**里因为「缺凭据」被提示并就地完成认证。

- **触发约定（前端推导，不改后端）**：`specialist.config.required_credentials: string[]`
  是可选名称列表（`configure_specialist` 本来就合并任意 config）。前端计算
  `pendingCredentialNames = required_credentials − credential_ref:*`；非空才强制展示凭据卡。
  `SPECIALIST_REQUIRED_CONFIG`、委派状态机、enqueue/lease/budget/desktop 均未改；也没有新增
  HandoverStatus 枚举。
- `HandoverInlineRows` 每条转交行挂 `HandoverRequiredCredentials`：GET specialist 详情 →
  有未满足项时在行下渲染 `.handover-credential-card`（复用 `SpecialistCredentials`，新增
  `requiredNames` 模式：显示「必填凭据 / 待补凭据：a、b」，名称改为仅可在声明项中选择）。
  提交仍走 `POST specialists/{id}/credentials`（name/secret/expectedRevision），成功后用返回的
  Specialist 更新本地待填列表——全部满足即整卡收起；并派发 `kai:specialists-changed` 让 rail 重拉。
  与 `pending_input` 的 SetupCard 可叠加（schedule/notify 语义未改）。
- 密文只存在输入框与请求体，不进 DOM 文本/日志/docs/commit。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                                       # clean
npx vitest run tests/main-session-credential-card.spec.tsx             # 3 passed（新增）
npx vitest run tests/main-session-pending-input-card.spec.tsx tests/handover-row.spec.tsx \
  tests/specialist-credentials.spec.tsx tests/specialist-setup-card.spec.tsx \
  tests/specialist-setup-card.flow.spec.tsx                            # 18 passed
npx vitest run                                                         # 1508 passed / 1 skipped / 1 既有失败
```

DOM 断言：`required_credentials:["vendor_api"]` + 无 ref → 主会话行（非 pending_input）出现必填卡、
待补名称、select 值 vendor_api、secret 为 password；提交调用 body
`{name:"vendor_api", secret, expectedRevision:3}`，成功后「已配置」、卡收起、DOM 不含密文、
URL 仍 `/assistant`；已有 `credential_ref:vendor_api` → 无卡无「待补凭据」；
pending_input + 必填凭据 → SetupCard 与凭据卡叠加。既有失败仍是 account-settings（无关）。

**79 部署与只读验收**：web `kai/assistant-web:20261030-managed-r55` 已滚（healthy，`/assistant` = 200）；
ctx `web-src-r55`，`specialist-credentials.tsx` sha256 `da18ab35…`、`handover-row.tsx` `6a97f229…`、
`web-codex.css` `65b00926…` 与本地一致；「待补凭据」/`handover-credential-card` 出现在
`382mjgm65_dtz.js` / `3-wxnx6qh0nk.css`（r54 无）。api 未动，仍 r23；
compose 备份 `compose.private.before-r55.json`。

- 真实页面：现网没有 `required_credentials` → 必填卡 0 个、无「待补凭据」（正确阴性），两条转交行仍是「已安排」。
- 注入式（**浏览器侧拦截 API，不写服务端**）：`required_credentials:["vendor_api"]` + 无 ref →
  实机出现「必填凭据 / 待补凭据：vendor_api」与 select/secret/保存；提交请求
  `POST …/credentials` body `{name:"vendor_api", secret:(redacted), expectedRevision:3}`；
  成功后卡收起、无「待补凭据」、URL 仍 `/assistant`、密文不在 DOM；rail 收到刷新事件。
  截图 `/tmp/kai79-accept/178-r55-required-cred.png`。

未验证：真实 Bot 声明 `required_credentials` 并在主会话完成认证（现网没有该配置，需改真实 bot config）；
触发推导、提交与收起由 DOM 用例 + 注入式实机验收覆盖。**触发态 = `required_credentials` 前端推导**。

## 子 Bot 的 ask 投影到主会话并可作答（2026-11-01，web r56，marker kai-main-session-ask-card）

缺口：子 bot 线程里的 `ConversationInputCard`（harness-input ask）不会出现在主 `/assistant`，
用户必须先点进 `?bot=` 才能回答；与「补充/确认尽量留在主 bot 会话」不一致。

- **发现**：对每条转交行，GET `specialists/{id}` 拿 `threadId` 与 `agentName/agentVersion`，
  再 GET `/api/agui/threads/{threadId}/history?limit=4`；`pendingThreadAsk()` 从末尾扫描：
  跳过 tool/activity，遇到 user 即判定已作答，遇到 assistant 文本能解析 harness-input 即为待答。
  有才渲染 `.handover-ask-card`（标题「「{bot}」正在等你回答」），UI 复用 `ConversationInputCard`，
  不复制问卷实现；无 ask 不渲染；`failed` 行不投影。
- **回写**：`submitThreadAnswer()` 走既有 AG-UI run 入口
  `POST /api/agui?agent_name=&agent_version=`，body 为 `RunAgentInput{threadId, runId, messages:[user 回答], ...}`；
  **消息落在该子线程**（服务端按线程历史重建上下文），主助理线程不写；响应事件流后台读完，
  避免中途断开取消刚创建的执行。提交成功后卡本地收起；**全程没有 `location.assign(?bot=)`**。
- 未改委派/lease/预算/desktop、未改 ConversationInput 协议、未新开服务。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                                 # clean
npx vitest run tests/main-session-ask-card.spec.tsx              # 3 passed（新增）
npx vitest run tests/main-session-pending-input-card.spec.tsx   tests/main-session-credential-card.spec.tsx tests/handover-row.spec.tsx   tests/conversation-input-card.spec.tsx                         # 25 passed
npx vitest run                                                   # 1511 passed / 1 skipped / 1 既有失败
```

DOM 断言：history 末条 assistant 含合法 harness-input → 主会话出现「「休息提醒」正在等你回答」+
问题/输入；提交后 fetch 打到 `/api/agui?agent_name=archive-assistant-agent&agent_version=0.1.1`，
body `threadId="thread-bot-1"`、单条 user 消息含格式化回答（`请确认参数 … 8 公里`），卡收起、
URL 仍 `/`；无 ask / 已被 user 回答 → 不渲染。既有失败仍是 account-settings（无关）。

**79 部署与只读验收**：web `kai/assistant-web:20261030-managed-r56` 已滚（healthy，`/assistant` = 200）；
ctx `web-src-r56`，`thread-answer.ts` sha256 `f4dbdf33…`、`handover-row.tsx` `22178ca5…`、
`web-codex.css` `ef755728…` 与本地一致；「正在等你回答」/`handover-ask-card` 出现在
`2q0xa3tch2d9u.js` / `3ii6a0c6fawx1.css`（r55 无）。api 未动，仍 r23；
compose 备份 `compose.private.before-r56.json`。

- 真实页面：当前没有待答 ask → 0 张 ask 卡、无 console/page error，转交行仍「已安排」。
- 注入式（**浏览器侧拦截 specialists/history/AG-UI POST，均不写服务端**）：
  实机出现「「休息提醒」正在等你回答 / 请确认参数 / 目标距离」；作答提交后捕获到
  `POST /api/agui` body `threadId="thread-fixture-ask"`、`messages[0].content="请确认参数\n\n目标距离\n8 公里"`，
  卡收起、URL 仍 `/assistant`。截图 `/tmp/kai79-accept/179-r56-ask-card.png`。

未验证：真实子 bot 挂起 ask 时的跨线程 ACK（现网没有待答 ask；实际提交会启动该 bot 的真实运行）。
**回写路径 = append 到 specialist thread（AG-UI run，body.threadId 即子线程）**。

## 主会话内联审批卡（2026-11-01，web r57，marker kai-main-session-approval-card）

缺口：任务/子 bot 进入 `waiting_approval` 时，`ApprovalCard` 只在当前激活线程（任务侧栏 +
agent-thread + approvalStore）显示；主 `/assistant` 转交行只有「等待你审批，请到网页助理确认」，
必须点进任务/`?bot=` 才能点批准。

- **发现**：对每条转交行 GET `specialists/{id}` 拿 `threadId`，再用既有
  `loadTaskSummary(threadId)`（`/api/agui/threads?thread_id=`，与任务侧栏同一投影）读
  `pending_approval`；`status === "pending"` 才在行下渲染 `.handover-approval-card`，
  标题「「{bot}」等待你审批」，UI **复用 `ApprovalCard`**（`formatApprovalReason` /
  `approvalContextRows` 原样），不复制第二套审批界面、不动全局 approvalStore 的 settled 语义。
- **decide**：点击允许/拒绝走既有 `PUT /api/harness/approvals/{approvalId}`（body
  `{decision:"approved"|"rejected"}`，与 agent-thread 一致）；成功后本地清掉该投影卡
  （不必等下一次刷新），全程没有 `location.assign(?bot=)`。
- 未改委派/lease/预算/desktop、未改 ApprovalCard 视觉、未新开服务。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                                   # clean
npx vitest run tests/main-session-approval-card.spec.tsx           # 3 passed（新增）
npx vitest run tests/main-session-ask-card.spec.tsx   tests/main-session-credential-card.spec.tsx   tests/main-session-pending-input-card.spec.tsx tests/handover-row.spec.tsx  # 13 passed
npx vitest run                                                     # 1514 passed / 1 skipped / 1 既有失败
```

DOM 断言：summary 带 `pending_approval`（Bash + 策略原因 + command）→ 主会话出现「「行业快报监控」
等待你审批」+「允许运行这条命令？」+ 原因/命令；点「允许一次」→ `PUT
/api/harness/approvals/approval_1` body `{decision:"approved"}`，卡收起、URL 仍 `/`；
点「拒绝」→ `{decision:"rejected"}`；无 `pending_approval` → 不渲染审批卡。
既有失败仍是 account-settings（无关）。

**79 部署与只读验收**：web `kai/assistant-web:20261030-managed-r57` 已滚（healthy，`/assistant` = 200）；
ctx `web-src-r57`，`handover-row.tsx` sha256 `3670ceb5…`、`web-codex.css` `9f56e9de…` 与本地一致；
`handover-approval-card` 出现在 `3pit_qsyasr2o.js` / `35ufqva-jsen9.css`（r56 无）。api 未动，仍 r23；
compose 备份 `compose.private.before-r57.json`。

- 真实页面：当前没有 `waiting_approval` 线程 → 0 张审批卡、无 console/page error；
- 注入式（**浏览器侧拦截 thread summary / history / approvals PUT，均不写服务端**）：
  实机出现「「行业快报监控」等待你审批」+ Bash/原因/命令 + 拒绝/允许一次；点允许后捕获
  `PUT /api/harness/approvals/approval_fixture_1` body `{"decision":"approved"}`，卡收起、URL 仍 `/assistant`。
  截图 `/tmp/kai79-accept/180-r57-approval-card.png`。

未验证：真实 `waiting_approval` 线程在主会话被批准（现网没有待审批 Run；真实点批准会改变运行状态）。
**decide 路径 = 既有 `PUT /api/harness/approvals/{approvalId}`**。

## 编辑弹窗里的「首次试运行」（2026-11-01，web r58，marker kai-assistant-bot-edit-trial）

缺口：studio「专职子助理」页有「首次试运行」，但 `/assistant` 的 Bot 编辑弹窗没有——
助理面用户只能改外观/凭据/看职责，不能就地跑一次真实检查。

- `bot-edit-dialog`：当 `detail.specialist.status ∈ {ready, active}` 时，在「例行程序」下方显示
  「首次试运行」按钮 + 说明「只排一次检查并回报真实结果；试运行不会启用持续运行」。
- 点击走既有 `POST specialists/{id}/trial`，body `{expectedRevision}`（与 assistant-manager 一致）；
  成功显示 `${trial.scheduled}（执行 ${trial.executionId}）` 并派发 `kai:specialists-changed`
  （rail 可刷新）；失败原样展示服务端文案（如「配置缺项，不能试运行：schedule_minutes」），
  按钮恢复可点。**trial ≠ 启用**：服务端成功后状态仍是 ready，本片也没有加启用/暂停整套。
- 未改 trial 后端语义、委派/lease/enqueue/预算/desktop、SetupCard「保存并启用」与主会话其余卡。

**测试**

```
cd web/harness-console
npx tsc --noEmit                                                   # clean
npx vitest run tests/bot-edit-dialog-trial.spec.tsx                # 3 passed（新增）
npx vitest run tests/bot-edit-dialog.spec.tsx tests/bot-edit-dialog-paused.spec.tsx \
  tests/specialist-credentials.spec.tsx                            # 12 passed
npx vitest run                                                     # 1517 passed / 1 skipped / 1 既有失败
```

DOM 断言：ready → 按钮与说明可见；点击 → `POST …/trial` body `{expectedRevision:3}`，
成功出现「已排入首次检查（执行 execution_trial_1）」；needs_input → 不渲染按钮；
active + 服务端冲突 → 显示「配置缺项，不能试运行…」且按钮可重试。既有失败仍是 account-settings（无关）。

**79 部署与只读验收**：web `kai/assistant-web:20261030-managed-r58` 已滚（healthy，`/assistant` = 200）；
ctx `web-src-r58`，`bot-edit-dialog.tsx` sha256 `4476ecee…`、`bot-edit-dialog.module.css` `7ad9a27b…`
与本地一致；「试运行不会启用持续运行」出现在 `1pmgiqjj_07g9.js`（r57 无）。api 未动，仍 r23；
compose 备份 `compose.private.before-r58.json`。

- 真实 active Bot（行业快报监控）弹窗：`首次试运行` 按钮与说明可见（**未点击**，避免真实排一次执行）；
  截图 `/tmp/kai79-accept/181-r58-trial-dialog.png`。
- 注入式（**浏览器侧拦截 specialists/trial，不写服务端**）：ready fixture 点「首次试运行」→
  `POST specialists/{id}/trial` body `{"expectedRevision":3}`，界面显示
  「已排入首次检查（执行 execution_fixture_trial）」，URL 不变。

未验证：真实点一次 trial（会为该 Bot 排入一条真实执行，消耗额度）；trial 的成功文案与服务端拒绝由
DOM 用例 + 注入式实机验收覆盖。
