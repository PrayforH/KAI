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

设计决策记录：[ADR](plans/2026-10-28-kai-dot-vm-bridge.md)；部署手册：[runbook](plans/2026-10-28-kai-dot-deploy-runbook.md)。

## 2. 测试证据（实际命令与结果）

隔离数据库：本机一次性容器 `kai-dot-test-postgres`（pgvector/pgvector:pg16，端口 127.0.0.1:35432），未触碰任何共享库。

```
HARNESS_TEST_DATABASE_URL=postgresql+asyncpg://harness:harness@127.0.0.1:35432/harness_test \
  uv run pytest tests/unit/assistant tests/unit/sandbox/test_computer_{runtime,vm_provider,desktop_client,viewer,vm_keys,vm_seed,ssh_gate,workspace}.py \
  tests/integration/storage/test_computer_{desktop,desktop_gate,vm_provider,registry,manager,access}_postgres.py
# 最终结果：195 passed（05:31）；storage 全量 190 过，其余 16 失败均为本地无 Redis/MinIO 的既有基础设施用例（pristine 分支同因失败）
```

专项断言覆盖：

- **桌面状态机**（8 项）：observe→agent→release、二次 acquire 冲突、接管 pending→human 定格、旧 epoch 被 fence、交还后 agent 可重新获取、TTL 惰性回落 observe、pending 宽限 ≤5s、无电脑行时 404。
- **控制服务 gate**（2 项，走真实 canonical 授权链：账号/租户/个人线程绑定/SandboxLease/Run fence）：当前持有者+当前 epoch → 200；epoch 过期 → 409；release 后不再自动授权（renew-only）→ 409；错误控制密钥 → 403。
- **RFB 客户端**（4 项）：3.3–3.8 握手与安全类型选择、SetPixelFormat（U8 bpp）、原始编码全帧 blit→PNG、Pointer/KeyEvent 线序与组合键、keysym 映射（ASCII/命名键/Unicode 0x01000000+）。
- **VM Provider**（25 项）：registry 路由按 canonical 行、未配置 provider fail-closed、adopt 幂等/冲突拒绝、容器验证（镜像/挂载/KVM 设备/label）。
- **VM 生命周期**（4 项）：ensure 认领并启动既有 runner、缺容器拒绝、reset 优先 guest fencing（VM 存活返回引用）、guest 不可达时物理停机回退（容器删除、盘不动）。
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
