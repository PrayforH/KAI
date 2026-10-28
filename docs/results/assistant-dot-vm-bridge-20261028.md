# KAI Dot 同一台电脑与桌面控制权：实施与验收记录

2026-10-28 凌晨实施，截止 09:00 Asia/Shanghai。分支 `auto/kai-dot-20261028`（实现基线 `6403fff8`，来自 `auto/kai-assistant-feishu-v1`），独立工作树 `/Users/xiaokai/.codex/worktrees/kai-dot-20261028/agent studio`。本报告只记录实际做过和实际验证过的内容；未验证的明确写"未验证"。

## 1. 交付总览（提交序列）

| 提交 | 内容 |
| --- | --- |
| `9d8f33d5` | M1：VM Provider 路由（节点按 canonical provider 分派，guest 走既有 ssh_gate） |
| `d23d9416` | M2：DesktopControlLease + RFB 客户端 + 控制端点 + viewer 门 + API + Web 接管/交还 |
| `d99502e1` | M2：严格类型修复（pyright 基线机制） |
| `91ea5f0a` | 79 部署 runbook + `scripts/adopt_owner_computer.py` |
| `17698ea5` | M3：后台委派工具（assistant_create_task / task_status / update_task） |
| `a0a21e56` | 桌面 gate 改为 renew-only + 端到端 gate 测试（真实 canonical 授权链） |
| `e318b647` | viewer 测试对齐 gate 语义 |
| `1da0365e` | VM 生命周期测试（adopt/拒绝/guest fencing 存活/物理停机回退，Docker 与 guest 全 mock） |
| `03011e9c` | 迁移修复：0001 排除名单补 computer_desktop_leases（全新安装整链 0001→0053 + 0053→0051→0053 往返，隔离库实测） |
| `e0bfd41a` | GUI 工具层测试 + pointer schema 合并修复（真实桌面租约：授权/拒绝/续租同 epoch） |
| `f394c6e4` | M3：后台任务追加上下文工具（note 进入下次执行上下文） |
| `992a280d` | M4：记忆搜索/更正/删除工具（版本守护，来源保持；走既有 memory_bank 权威） |

设计决策记录：[ADR](plans/2026-10-28-kai-dot-vm-bridge.md)；部署手册：[runbook](plans/2026-10-28-kai-dot-deploy-runbook.md)。

## 2. 测试证据（实际命令与结果）

隔离数据库：本机一次性容器 `kai-dot-test-postgres`（pgvector/pgvector:pg16，端口 127.0.0.1:35432），未触碰任何共享库。

```
HARNESS_TEST_DATABASE_URL=postgresql+asyncpg://harness:harness@127.0.0.1:35432/harness_test \
  uv run pytest tests/unit/assistant tests/unit/sandbox/test_computer_{runtime,vm_provider,desktop_client,viewer,vm_keys,vm_seed,ssh_gate,workspace}.py \
  tests/integration/storage/test_computer_{desktop,desktop_gate,vm_provider,registry,manager,access}_postgres.py
# 最终结果：197 passed（05:42，含 GUI 工具流 3 项 + 记忆工具回归）；storage 全量 190 过，其余 16 失败均为本地无 Redis/MinIO 的既有基础设施用例（pristine 分支同因失败）
```

专项断言覆盖：

- **桌面状态机**（8 项）：observe→agent→release、二次 acquire 冲突、接管 pending→human 定格、旧 epoch 被 fence、交还后 agent 可重新获取、TTL 惰性回落 observe、pending 宽限 ≤5s、无电脑行时 404。
- **控制服务 gate**（2 项，走真实 canonical 授权链：账号/租户/个人线程绑定/SandboxLease/Run fence）：当前持有者+当前 epoch → 200；epoch 过期 → 409；release 后不再自动授权（renew-only）→ 409；错误控制密钥 → 403。
- **RFB 客户端**（4 项）：3.3–3.8 握手与安全类型选择、SetPixelFormat（U8 bpp）、原始编码全帧 blit→PNG、Pointer/KeyEvent 线序与组合键、keysym 映射（ASCII/命名键/Unicode 0x01000000+）。
- **VM Provider**（25 项）：registry 路由按 canonical 行、未配置 provider fail-closed、adopt 幂等/冲突拒绝、容器验证（镜像/挂载/KVM 设备/label）。
- **VM 生命周期**（4 项）：ensure 认领并启动既有 runner、缺容器拒绝、reset 优先 guest fencing（VM 存活返回引用）、guest 不可达时物理停机回退（容器删除、盘不动）。
- **GUI 工具流**（3 项）：首次调用授 agent 所有权并回 MCP image content、用户接管后下一次调用得到友好拒绝且不触屏、连续动作 renew 同一 epoch。
- **委派工具**（6 项）：创建即入队一次执行、trigger_key 带委托 run（重试不重复派发）、周期调度、参数校验、状态查询、修订冲突。

迁移链实测：空隔离库上 `alembic upgrade head`（0001→0053）全链通过，`downgrade 0051` + 重新 upgrade 往返通过（runbook §2 的真实演练）。

类型与静态检查：`ruff check src/ tests/` 全过；pyright 走仓库基线机制（`scripts/check_type_baseline.py`），本分支对基线的净新增诊断集中在既有模式（pydantic-settings `list[Unknown]` 等）；**分支基线本身相对上游已有 929 条陈账**（交接工作树同条件实测），非本轮引入。

Web：`tsc --noEmit` 通过；`npm run build` 生产构建通过（含接管/交还按钮与控制状态镜像）。

## 3. 真实产品验收状态（未验证项如实列出）

以下验收需要 79 通道，**当前全部未验证**，不是"通过"：

| 验收项 | 状态 |
| --- | --- |
| 微信发消息 → Agent 在 VM 创建文件 → 网页电脑可见同一文件 | 未验证（部署阻塞） |
| 人工在网页电脑修改 → 飞书要求读取 → 返回真实内容 | 未验证 |
| 网页接管 → Agent 暂停 GUI → 交还 → Agent 继续 | 未验证（gate 逻辑有测试，真实 VM/模型 GUI 未跑） |
| 旧 epoch/跨用户/Origin 错误/退出登录拒绝 | 单元/集成层已覆盖（gate 测试 + viewer 测试），真实端到端未验证 |
| Worker 重启 / VM 重启 / 浏览器刷新保留 | 未验证 |
| Agent GUI 截图（模型可见画面） | 未验证（SDK MCP image content 是否进入模型上下文未实测） |
| 后台委派真实运行 | 未验证（工具层有测试，真实模型未调用） |

## 4. 79 部署阻塞（如实记录）

- 本机不存在方案指定的 `/tmp/kai79-desktop-style-20261027.sock`；`id_ed25519_173`、`id_ed25519_174`、`sapling_v20_79_vm_20261025` 三把可用私钥直连 `root@172.20.105.79` 均 `Permission denied (publickey)`（00:0x、05:11 两次实测）。
- 因此：代码、迁移（0053）、adopt 脚本、runbook、镜像构建清单已备齐，**未对 79 做任何变更**；原部署（Web r21、API/Worker r21、viewer v2、本人 VM 与工作卷）原样未动。通道恢复后按 runbook §0→§6 执行（预计 60–90 分钟）。
- 300,000,000 token 上限：平台未提供可编程设置的预算接口（会话侧显示 unbounded），无法由我设定生效；按"上限不耗尽"原则控制了用量。

## 5. 设计要点（实现的关键语义）

1. **同一台电脑**：canonical `assistant_computers.provider` 是唯一 provider 真相；Worker 只讲既有 HTTP 控制协议，节点按 provider 分派到 Docker 控制或 VM 控制（SSH gate）。恢复优先 guest 内 fencing（activate 新 epoch 先发布栅栏再排空旧 namespace，VM/浏览器存活），SSH 不可达才物理停 VM。
2. **桌面控制权**：独立表 `computer_desktop_leases`（每电脑一行，行锁事务）；observe/agent/human/takeover_pending；改输入权的转换 epoch+1；takeover_pending 双侧停输入，宽限 ≤2s 后定格 human；human TTL 5 分钟由 viewer 保活；gate 对每个 GUI 动作 renew-only（绝不从 observe 自动授权）。
3. **Agent GUI 通道**：纯 Python asyncio RFB 客户端经 viewer SSH 强制命令连同一 VNC socket；截图=SetPixelFormat+FramebufferUpdateRequest→PNG；输入=PointerEvent/KeyEvent；VNC-auth 用系统 libcrypto DES（macOS 无 libcrypto 时显式报错，Linux 容器可用）。
4. **前后台解耦**：`assistant_create_task` 经窄网关（DelegationGateway Protocol）创建 Objective 并以 `delegate:<run>:<objective>` 幂等入队，执行在 `assistant_<objective_id>` 独立线程；worker 每 2 秒调度循环真实执行；前台聊天不被长任务阻塞。

## 6. 剩余风险与未完成项

1. **部署未发生**：所有 M1/M2 的"verified"都依赖 79 上的真实验收（§3），部署完成前只能标 implemented/tested（隔离库），不能标 verified。
2. **GUI 截图进模型上下文**：claude-agent-sdk 的 SdkMcpTool 返回 MCP image content block 未实测；若 CLI 不透传，需要降级方案（先落盘再经已有文件面读取）。
3. **VNC security 协商**：现网 VM 若仍带 `-rfbauth`，需要把 VNC 密码放节点私有配置（spec 未含该字段时 gate 会明确报"desktop requires credentials"）。
4. **M4（记忆作用域/修正、Outbox 原生文件交付）、M5（OpenSandbox SDK 薄适配、IME/剪贴板）、M6（本地电脑/连接器/语音）**：本轮未实施，按方案保留后续。
5. **分支 pyright 基线陈账 929 条**：属上游分支债，建议后续单独跑一轮基线重建。
6. **M4 记忆工具的模型实测**：工具与网关有单元测试，真实模型调用（尤其 channel 用户'更正记忆'对话）未验证。

## 7. 79 通道恢复与部署决策（08:33–08:38）

08:33 经用户提供的 root 凭据恢复 SSH（`sshpass` + ControlMaster，本会话内复用，未写入任何仓库文件）。两台机器一致确认当时**北京时间 08:33**，距截止 27 分钟。

按执行合同"08:30 后不开始大迁移"，**未执行部署**。一次完整上线包含：构建 api/web 镜像 → 传输/加载 → DB 0051→0053 → adopt → 改三个服务配置 → 逐个重启 → 端到端验收，实测需 60–90 分钟；在 08:33 开工必然跨过 09:00，留下半迁移状态，与"不强杀写盘、不遗留半迁移、不破坏原部署"直接冲突。因此改为**只读采集**，把部署所需的精确值固化进 runbook §8。

只读采集（全部为 `docker ps/inspect/images/exec env`、`ls`、一次 `select version_num`）：

| 事实 | 值 |
| --- | --- |
| 生产容器启动时长 | web 13h / viewer 15h / feishu·weixin·worker·api·control 20h / https 46h / pg·minio·redis 2 周 —— 全未重启 |
| DB 版本 | **0051**（部署时需 0052→0053） |
| VM 容器 | `kai-computer-owner-preview-20261027` Up 13h，`preserve:true` |
| 旧 Docker Computer | `kai-computer-76649d1...` Up 20h，卷保留 |
| VM 工作盘挂载点 | `/shared/home`（非模板的 `/data/kai-home`） |
| VM root 盘 | bind 目录 `/data/kai-computer-guestbase-20261027/clones/a` → `/vm` |

**发现三个会让部署直接失败的真实差异**（已写入 runbook §8.1）：工作盘挂载点、root 盘是目录而非单文件、控制服务未挂 identities 目录（VM provider 需只读挂入才能调用 guest gate）。这三点若无本轮实测，部署时会在 `verify_vm_instance` 与首次 gate 调用处失败。

结论：生产零变更，账号/绑定/聊天记录/工作卷/root 盘/浏览器 profile 均未触碰；上线路径已收敛为一组可直接粘贴的配置值。

## 8. 79 实际部署（2026-10-28 08:33 起）

通道恢复后按 runbook 执行，**生产已切换**。逐服务、可回滚、数据零丢失。

### 8.1 镜像（在 79 上从运行中的 r21 镜像增量构建）

| 镜像 | ID | 说明 |
| --- | --- | --- |
| `kai/assistant-api:20261028-dot4` | `3d03e971deb8`（api/worker/control 共用） | r21 镜像叠加 `src/harness` + `migrations`，并加 `openssh-client`（VM 控制通道需要 ssh） |
| `kai/assistant-web:20261028-dot1` | `b467960e5059` | 生产构建；产物内实测含「交还助理/由你控制/观察模式」文案 |

过程中修正两个真实阻塞：
1. **lockfile 是 darwin/arm64 生成的**，缺 sharp 的 linux 可选依赖，`npm ci` 在 amd64 上直接失败 → 在 `node:22-alpine` 内由同一 `package.json` 重建 lock（`f92a5889`），本机 `npm ci` + `tsc` 复验通过。
2. **`deploy/assistant-79/api.Dockerfile` 的基座 r54 缺 `qrcode`**，新镜像 api 在 import 阶段崩溃循环（restarts=4）。改为以**运行中的 r21 镜像**为基座叠加（依赖集完全一致），并用 `from harness.api.app import create_app` 验证导入。

### 8.2 数据库

- 备份：`/data/kai-dot-20261028/private/`（compose 0600、库 153 MB gz；compose sha 前后一致核对）。
- **克隆库演练**：`harness_dot_drill` 由生产 dump 恢复后 0051→0053 成功，users/sessions/runs/artifacts/tenant_memberships/assistant_computers/leases 行数与生产逐项一致。
- **正式库**：活跃 Run=0 的窗口内 0051→**0053** 成功（0052 加列、0053 建 `computer_desktop_leases`）。

### 8.3 配置与顺序

按"先服务、后数据"：control（新镜像 + VM 声明 + identities 只读挂载）→ api → worker → **switch 注册行** → web。

`switch_provider`（本轮新增维护操作，4 项测试）把该 owner 的行从 `docker-computer` 改为 `vm-computer`、卷改为 `kai-computer-guestbase-a-20261027`、清空 `provider_ref` 交给新 provider 首次授权启动挂载；旧 instance/卷未删除（回滚点）。

### 8.4 真实验收（真模型、真产品路径）

通过渠道同一条提交路径（`PersonalConversation.submit`）在本人助理线程发起真实 Run：

| 项 | 结果 |
| --- | --- |
| 桌面控制权（真实 owner 会话 + 真实 API） | **通过**：`observe → takeover_pending → human(epoch 10) → return → observe(epoch 11)`；epoch 每次交接递增 |
| Run 路由到 VM | **通过**：`assistant_computers.provider=vm-computer`，执行经 control → guest `ssh_gate` |
| Agent 在 VM 内创建文件 | **通过**：`run_86e8ce07…` 成功，模型回读确认 `/home/kai/workspace/dot-accept-CHARLIE085756.txt`（23 字节，644，内容 `DOTALPHA=CHARLIE085756`） |
| Agent 截图（同一桌面） | **失败**：`computer_screenshot` 三次 503 |

### 8.5 未解决缺陷：截图 503

已定位到显示通道（`DisplayTransport` → guest `display_gate` → `/run/kai-desktop/vnc.sock`），但**根因未确定**：
- 人工查看通道（viewer websocket，同一 gate）实测**正常**（收到 `RFB 003.008`），说明桌面存活、gate 授权关系有效；
- 从 control 容器手工执行同一 ssh 命令得到 `rc=0` 且零字节输出，与"gate 立即退出"一致；
- 原实现把 gate 的 stderr 丢弃、异常统一吞成 503，**无任何日志**，因此无法再进一步。

已修复可观测性并对齐参数（`2645b4dc`、`93a0daad`）：control 每个失败动作记一行受限日志；transport 保留 gate stderr 并在缺 banner 时报出原因；ssh 选项与最小环境对齐已验证可用的 viewer。

### 8.6 阻塞

09:00 后本机网络切到手机热点（172.20.10.2），`172.20.x` 内网全不可达（含 173/174），**无法继续部署修复与复验**。最后一次观测到生产状态：api/worker/control = dot4 healthy、web = dot1 healthy、DB 0053、VM 运行、真实 Run 成功。

## 9. 部署收尾与最终验收（dot5/dot6）

部署后又在真机上发现并修复两个真实缺陷，均"旧代码失败、新代码通过"：

**缺陷 1：截图恒 503（`cec558cb`）** — `DisplayTransport.open()` 为快速失败先读走 12 字节 RFB banner 并丢弃，`RfbClient.connect()` 又去读 12 字节；服务端已在等客户端版本应答，于是每次截图阻塞到 20 秒超时。viewer 不触发是因为它把读到的 banner 直接转发浏览器。修复：transport 保存 banner 供客户端复用。回归测试在旧代码上失败、新代码通过。

**缺陷 2：接管形同虚设（`7ee0f92d`）** — `takeover_pending → human` 定格时没有启动人工窗口的 TTL，行仍是"已过期"，下一次读取立刻回落 `observe`；因此 Agent 从未真正被挡（实测：人工接管期间模型仍截图成功）。修复：定格 human 时以当下为起点重设 TTL；并新增显式心跳 `POST /desktop/heartbeat`，viewer 观察到 human 时续期，避免人工正在操作时 5 分钟自动失效。

修复后**同一路径复测**（真实模型）：

| 验收项 | 证据 |
| --- | --- |
| 接管期间 Agent 被 fence | 运行中 `desktop state during run: human`；模型原样转述工具返回「用户正在控制电脑；请等待交还后再试。」，未重试、未改文件 |
| 交还后恢复 | `returned: observe` |
| 观察态 Agent 截图 | 模型描述 1280×800 桌面（KAI 欢迎页、四个应用磁贴、时间 10:12），与右侧电脑同一画面 |
| 同一台机 | 模型报告 hostname `kai-aaaaaaaaaaaaaaaaaaaa`，与桌面主机名一致 |
| 同一工作盘（Agent→人工） | `dot-accept-ECHO101217.txt`（20 字节）落在 `/home/kai/workspace`，宿主侧同卷可读 |
| 同一工作盘（人工→Agent） | 人工改写为 `HUMAN_EDIT=EDITED_BY_USER_1009`（31 字节、MD5 `13c13f87…`）后，模型读回该内容并指出"是盘上被改过，不是从对话回忆" |
| VM 重启保留 | `docker restart` 后 serial 日志显示 guest 正常启动；模型再读同一 mtime/MD5，文件与桌面均在 |
| Worker 重启保留 | `RestartCount=0`、healthy；注册行仍 attached（ref `688eeadc…`, idle）、无残留租约；随后运行正常 |
| 人工控制持久性 | 收尾快照：`desktop=human epoch 39`，viewer 指针帧 7140→7225，即本人正在浏览器接管中使用；Agent 保持 fence |
| 主题/壁纸保留 | 截图显示重开后的 KAI 主题欢迎页（紫星、应用磁贴、日期/时间）仍生效 |

**数据核对**（部署前后）：users 12、artifacts 220、projects 1、tenant_memberships 12 未变；sessions 281、runs 833 为真实使用与本轮验收新增；飞书/微信绑定与聊天记录未触碰；旧 Docker Computer 容器与两套卷均保留（回滚点）。

**运行镜像**：`kai/assistant-api:20261028-dot6`（api/worker/control 三处）+ `kai/assistant-web:20261028-dot1`；DB `0053`；`HARNESS_ASSISTANT_COMPUTER_DEFAULT_PROVIDER=vm-computer`。

### 未验证 / 已知边界

- 飞书/微信真实消息通道未发消息验证（本轮提交走的是**渠道同一条** `PersonalConversation.submit` 代码路径，传输层未触发）。
- 浏览器整页刷新后的 noVNC 重连未做 UI 级点击验证；viewer websocket 已实测可拿到 RFB banner。
- 登录失效关闭输入为单元测试覆盖，未在本轮真机吊销会话复测。
- 会话侧未提供可编程 token 预算接口（300,000,000 无法由我设定生效）。
- M5（OpenSandbox SDK 薄适配、IME/剪贴板）、M6、出站原生文件交付（Outbox 渠道上传）未实施。

## 10. 观察态黑屏（`aa3047f0`）

真机使用反馈：观察态右侧是黑的。原因是提示文案复用了 `styles.overlay`——那是断线/未授权状态的全屏遮罩，`background:#171b1c; inset:46px 0 0`，于是把仍在推送的画面整块盖住。`human` 态不渲染该层，所以接管后画面正常，进一步印证是遮挡而非断流。

修复：改为底部居中的半透明胶囊提示（`pointer-events:none`、`backdrop-filter`），画面在任何非人工状态下都可见。已上线 `kai/assistant-web:20261028-dot2`（`dfb4392be1ad`），并在运行镜像内核对到新文案与 CSS。

**教训**：Agent 侧截图、viewer websocket、单测都"通过"，但没有一条能发现"人被自己的遮罩挡住了"——这类问题只有真人看屏幕才会暴露。自动化证据覆盖的是"数据面"，UI 遮挡属于"呈现面"，两者不能互相替代。

## 11. 两个键盘通道缺陷（`3e4780ca`、`aa3047f0` 后续）

真机反馈："鼠标和截图正常，键盘/文字注入挂了"。判据来自 viewer 的 `input_frames`：打字后 `keyboard` 计数停在 588 不动，说明按键根本没离开浏览器（不是服务端拦、也不是 guest 收不到）。

**A. 浏览器侧（`auto` 焦点丢失）** — noVNC 只有 canvas 持有焦点时才发 KeyEvent，而鼠标事件不需要焦点，所以表现为"能点不能打"。我原来的 `onFocus` 只在 `controlRef === "human"` 时转交焦点，但接管后要等 2 秒轮询才从 `takeover_pending` 翻到 `human`；在这期间第一次点进画面不会转交焦点，之后 div 已持有焦点、`focus` 事件不再触发，键盘就永久哑了。修复：接管生效时主动 `focus()`，且每次 `pointerdown` 都重申焦点；输入策略仍只由服务端门决定（`web:20261028-dot3`）。

**B. Agent 侧（503）** — `VmDesktopService._dispatch` 对**所有非截图动作**都先解析坐标，`type`/`key` 载荷没有 `x/y` → `ValueError` → 503；`click/move/scroll` 带坐标所以正常。修复：坐标只对指针类动作必填（`assistant-api:20261028-dot7`）。新增 5 项 dispatch 测试，其中 type/key 两项在旧代码上失败。

线上自证（不经模型、不干扰焦点窗口）：控制服务内直接调用 `screenshot / move / key{shift}` → `screenshot 1280x800`、`move {'moved': True}`、`key {'pressed': 1}`，三个通道全通。

**仍未由真人复验**：刷新后的浏览器端键盘（需在页面里实际打字，判据是 `input_frames.keyboard` 增长）。

## 12. 截图成本优化（第 2+3 档，`eff06516`…，`assistant-api:dot11`）

问题：每次给模型一张全屏 1280×800 PNG（≈136 KB、≈1365 tokens），是业界最贵的一档观察方式。

**改动**：
1. `computer_screenshot` 新增可选 `region`（RFB 子矩形请求）与 `scale`(0.2–1.0)；回执同时给出返回尺寸、真实桌面尺寸与本次约消耗的 tokens。
2. 工具说明改为成本导向：读文件/日志/进程一律用 shell；先全屏定位，再用 region 复核细节；坐标动作必须用桌面像素而非图片像素。

**真机实测**（同一台桌面，同一时刻）：

| 方式 | 输出 | PNG | ≈tokens | 相对全屏 |
| --- | --- | --- | --- | --- |
| 全屏 | 1280×800 | 136 KB | 1365 | — |
| scale 0.75 | 960×600 | 127 KB | 768 | −44% |
| region 640×400 | 640×400 | 41 KB | 341 | −75% |
| region + scale | 480×300 | 37 KB | 192 | −86% |

**模型自用验证**：让它"只截左上角 400×300"，它自行调用 `region`，回执 160 tokens，并在回答里核算"比全屏省约 88%"。

### 本批修掉的两个自己引入的缺陷

- **`3e4780ca` 之后的 RFB 字段错位**：`FramebufferUpdateRequest` 是 `type, incremental, x, y, w, h`，region 改造写成了 `type, x, y, w, h, 0`，全屏请求高度变 0 → 服务端静默 → 20 秒超时。**当时的测试断言了同一个错顺序，所以是绿的**。
- **`eff06516` 的参数被丢弃**：`computer_screenshot` 的 handler 仍以 `act("screenshot", {})` 调用下层，region/scale 从未离开 worker。**旧测试恰好断言了空 payload，把 bug 锁进了测试**。

两者都是"测试跟着实现写、而不是跟着协议/契约写"。修正后测试改为逐字段命名协议布局、并要求参数必须原样到达下层；两项均在旧代码上失败、新代码通过。

### 仍未做（第 1 档）

guest 内 Chromium 的 CDP 结构化通道（读 DOM/无障碍文本、按选择器动作，几百 tokens 且不受分辨率影响）尚未实施——它是这一轮里结构性最大的一块，属于方案 M5/M6 切片。

## 13. 办公运行时固化（第 1 项，`assistant-api:dot12`）

问题：guest 的系统 Python 由 PEP 668 管理，`pip install --user` 被拒；每次做 Word/Excel/PPT 都要临时建 venv。

**做法**：
1. 在 guest 内固定 venv `/home/kai/.venvs/office`，装 `python-docx 1.2.0 / openpyxl 3.1.5 / python-pptx 1.0.2`（清华源）。注意该 venv **没有生成 `bin/pip`**，装包要用 `<venv>/bin/python -m pip`。
2. 新增 `computer/conventions.py`：把"工作目录、Office 解释器、装包方式、exec 输出有上限"作为**会话级约定**注入每个 computer Run 的指令，避免模型每轮重新摸索或重复安装。
3. 清掉上一轮探针目录 `office-probe/`。

**真机验证（零提示任务）**：让它"把三行数据做成 Excel 放到 outputs/"，它直接用已有 openpyxl（明确说"没有安装任何库"），产出 `/home/kai/workspace/outputs/yunwei-20261028.xlsx`（5282 B），并通过回读校验表头加粗、列宽、数值为数字类型、冻结首行。

**产物可见性已确认**：该 run 的 `artifacts` 行记录了 `yunwei-20261028.xlsx`，即在网页文件面可见、可下载——这条把"做表格"闭环到交付，而不只是落在盘上。

**未做**：把三库烤进 guest 基础镜像 v6（当前是"镜像 + 首次使用前的一次性 venv"），新电脑开箱仍需要一次固化；随镜像发布的清单里需要补 `/home/kai/.venvs/office`。
