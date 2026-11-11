# 云电脑资源与生命周期（云 VM 停机保留磁盘）

日期：2026-11-12 · 分支：`auto/bot-runtime-alignment-20261109`
范围：仅云 VM（`vm-computer`）。远程电脑本轮不睡眠、不关机。

## 现状缺口（改前）

1. VM 只有 `ensure`（启动已声明的容器）与 `reset`（恢复；访客栅栏失败时
   走 `_stop()`，会 `DELETE` 容器）。没有“空闲停机”路径：VM 一旦启动就一直
   占着宿主机资源。
2. 空闲时长不可配置，也没有周期性清扫；只有每 Bot 桌面的 guest session
   回收（`reclaim_desktops`），不涉及 VM 电源。
3. 若 VM 被停机，下一次执行不会自动唤醒：`manager.ready()` 看到 `idle`
   直接返回，`connect` 直接要求访客确认执行，SSH 已不可达。
4. 注册/建号路径本身不会开机（`registry.ensure()` 只建身份行），首次真正
   Computer Use 才由 `manager.ready()` 启动；但没有“休眠后首次使用再唤醒”
   的幂等启动路径。

## 实现（改后）

### 数据与状态

- `ComputerState` 新增 `suspended`；`assistant_computers` 的
  `ck_assistant_computer_state` / `ck_assistant_computer_attached` 同步放宽
  （`suspended` 也要求 `provider_ref IS NOT NULL`）。迁移
  `0075_computer_vm_suspended`。
- `suspended` 只表示“容器已停、实例与工作盘/身份仍在”；不是回收，不换
  `volume_ref`，不删容器，不动磁盘。

### 停机（保留磁盘）

- 节点新增维护接口 `POST /v1/computer/vm/suspend`
  （`VmPowerRequest{tenant_id,user_id,idle_seconds}`，走私有 secret 鉴权）。
- `VmComputerLifecycle.suspend_idle()`：
  1. `ComputerRegistry.suspend_gate()` 在**电脑行锁**内校验：provider 为
     `vm-computer`、状态 `idle`、`provider_ref` 存在、无未过期的活动租约、
     无 `agent/human/takeover_pending` 桌面控制、无 `created/starting/busy`
     桌面槽（即将开始的占用），且
     `coalesce(last_active_at, updated_at)` 早于空闲阈值。
  2. 行锁保持到 Docker 停机确认之后，执行租约无法插进来。
  3. `_halt()` 只 `docker stop`（`t=30`）并等待 `Running=false`，**不**
     `DELETE`、不碰挂载；容器缺失则报错且不标记状态。
  4. 停机确认后把行标记为 `suspended`（同一事务提交）。
- Worker 侧 `ComputerRuntime.suspend_idle_computers()` 只筛选
  `provider=vm-computer` 的候选（远程电脑显式跳过），逐个请求节点；节点
  返回 409（忙/被接管/未够闲）时保留运行，下次再试。
- Worker 维护循环（60s）调用，设置项：
  `HARNESS_ASSISTANT_COMPUTER_VM_IDLE_SECONDS`（默认 `1800`，0 关闭）。
  沿用现有 `.env` 映射，无需新迁移以外的基础设施。

### 唤醒（首次 Computer Use）

- `VmComputerDriver.connect()` 在授权执行后先调
  `lifecycle(token, "wake")`：节点对 `execute` 租约执行幂等的 `_ensure()`：
  校验声明与磁盘挂载，容器在则启动、不在则明确拒绝（Run 不建盘/不建容器），
  只有本次真正启动时才等 SSH 就绪；已在运行的 VM 不做额外探测，避免给
  已有时延修复加负担。
- `manager.ready()` 把 `suspended` 视为就绪（不触发恢复）；
  `claim_execution()` 允许从 `suspended` 进入 `running`，随后由 `connect`
  唤醒。唤醒失败仍走原有租约过期→恢复路径，不会把半套状态交给下个 Run。

### 不受影响

- 单执行者队列、占用租约、人工接管、`Run 不直接建盘` 均未改。
- 远程电脑（`remote-computer`）不进入候选、不调唤醒。
- 群聊演示群与前端未改；本轮无 UI 电源状态（若以后加，文案须说明
  “保留工作盘/身份，丢掉未保存的临时进程”）。

## 验证

- 单元：`tests/unit/sandbox/test_computer_vm_suspend.py`
  - 空闲 VM 停机：`stop` 调用 1 次、无 `DELETE`、容器仍在、状态
    `suspended`、返回原 `provider_ref`；
  - 已停机 VM 不重复 stop；容器缺失时报错且状态不变；
  - 唤醒：停着的容器会 start 并等待 SSH；已运行的容器不 start、不探测；
  - 执行连接顺序为 `wake → activate`；
  - `manager.ready()` 对 `suspended` 不触发恢复；
  - 清扫只为 `vm-computer` 发请求，远程电脑被跳过，0 表示关闭。
- 控制面：`/v1/computer/vm/suspend` 未带 secret 返回 403；未声明 VM 时
  带 secret 返回 404。
- 集成（`tests/integration/storage/test_computer_vm_host_postgres.py`，
  真实数据库行锁与租约）：空闲停机成功并标记 `suspended`、容器仍在；
  未够闲、执行中、人工接管、桌面 `starting` 均拒绝停机；下一次执行从
  `suspended` 唤醒成功。
- 手工（部署后）：
  1. 把空闲阈值临时调小（如 120s），启动一个 VM 并让它空转；
  2. 观察日志/DB：状态转 `suspended`，`docker inspect` 显示容器存在但
     `Running=false`，`volume_ref` 与挂载不变；
  3. 再发一次 Computer Use：容器被 start，执行继续，同一条身份/磁盘；
  4. 在 VM 有活动执行或人工接管时等待阈值：不得停机（409/状态仍 running）；
  5. 远程电脑执行：状态与行为不变，不被清扫。

## 未做 / 待决策

- 长期回收、宿主机开机上限、例程预唤醒：本轮不实现。
- “预览 Bot 桌面”若发生在 VM 已休眠时，当前会因 runner 未运行而失败；
  是否让预览也唤醒留待产品确认（首轮未改）。
- 79/116 本轮均未发布此改动；等 116 补验完成且本条可验收后再协同上线。
