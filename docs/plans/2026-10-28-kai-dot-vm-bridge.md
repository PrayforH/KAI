# KAI Dot 同一台电脑与桌面控制权：实施 ADR

2026-10-28 凌晨，接续[完整方案](2026-10-28-kai-dot-complete-design.md)。本文件记录 M1/M2 的代码级决策与变更清单；验收结论另出报告。

## M1：Agent 与人工预览共用同一台 VM

### 决策

1. **控制通道不变，节点端按 provider 分派。** Worker 侧驱动仍然只讲既有 HTTP 协议（`/v1/computer/control`、`/v1/computer/lifecycle`、`/v1/computer/image/check`）；`control_server.py` 依据 `assistant_computers.provider` 把请求分派给 `DockerComputerControl/Lifecycle`（原样）或新的 `VmComputerControl/VmComputerLifecycle`。Worker 容器永远不持有 guest SSH 私钥。
2. **VM guest 控制走既有 `ssh_gate.py`。** `kai-control` 强制命令已经固定为 `sudo -n /usr/bin/python3 -I /opt/kai-computer/ssh_gate.py`，协议为 `{version:1, action: init|activate|renew|deactivate|exec, spec:{epoch, lease_id, ...}}`。`VmComputerControl` 用节点 identities 目录里该电脑的 control 私钥发起 SSH（`ControlMaster` 复用连接），不新增 guest 代码、不改 guest 授权。
3. **恢复（recover）优先不关 VM。** 新 recovery lease 的 epoch 大于旧 state，因此 `activate` 能先发布新栅栏并排空旧任务 namespace，再 `deactivate` 释放；VM 与浏览器保持运行。仅当 SSH 不可达时才走 `lifecycle("reset")` 物理停机（停容器，盘不动）。`complete_recovery` 返回存活 provider_ref，或确认 absent 返回 None。
4. **生命周期采用"认领既有资源"模式。** 节点私有配置声明 `vm_computers`（按 volume_ref）：容器名、SSH 地址/端口、runner 镜像、root 盘 bind、工作卷、seed 目录、KVM 设备 inode。`ensure` 校验既有容器（镜像、挂载；若容器带 owner label 则必须一致；无 label 的 compose 托管容器要求显式 `adopt` 标志），启动并等待 SSH 可达。新建 VM（从 base 派生 overlay）本轮显式不支持——能力缺失返回明确错误，不做伪装。
5. **canonical 注册表迁移用 `adopt`。** `ComputerRegistry.adopt(tenant, user, ...)` 仅维护入口可调（不发 API）：为既有 VM 建行（provider="vm-computer"，volume_ref=既有工作卷名，provider_ref 延后由 ensure 写入）。旧 Docker computer 行保留只读事实，不再被 Worker 选中：`ComputerRuntime` 按 `(tenant,user)` 查到的 computer.provider 选择驱动。
6. **91 端口语义不变。** `sandbox_enforcement` 增加 `vm-computer → DELEGATED`（VM 内 supervisor 的 namespace 栅栏与 Docker 版同源）。

### 变更文件

- `computer/vm.py`：`VmComputerDriver`（worker 侧，HTTP 协议 + provider="vm-computer"）。
- `computer/vm_host.py`：`GuestGate`（SSH 控制传输）、`VmComputerControl`、`VmComputerLifecycle`（节点侧）。
- `computer/control.py` / `control_server.py`：provider 路由；控制服务配置增加 VM 节点声明。
- `computer/registry.py`：`adopt`。
- `computer/access.py`：sandbox lease provider 校验按 computer.provider。
- `computer/runtime.py` / `config.py` / `composition.py` / `sandbox/base.py`：多驱动解析、默认 provider、enforcement 映射。

## M2：DesktopControlLease 与接管/交还

### 状态机

`observe → agent | human`；`agent|human → takeover_pending → human`（接管）；`human|takeover_pending → observe`（交还）。每次改变输入权的转换 epoch+1。agent 只能从 observe 获取；human 由 viewer TTL 保活，过期惰性回落 observe。takeover_pending 期间双侧新 GUI 动作都被拒。

### 决策

1. **独立表** `computer_desktop_leases`（每台电脑一行，行锁事务）：state、owner（agent 时为 sandbox id）、epoch、revision、expires_at。迁移 0053。
2. **Agent GUI 动作经控制服务新端点** `/v1/computer/desktop`：先验 execution lease holder（复用 `registry.holder`），再验 desktop lease（state=agent 且 epoch 与动作绑定）。执行用**纯 Python asyncio RFB 客户端**（`computer/desktop_client.py`）通过 viewer SSH key + `display_gate.py` 连接同一 noVNC socket：SetPixelFormat→FramebufferUpdateRequest 取截图，PointerEvent/KeyEvent 注入输入。支持 None 与 VNC-auth（内嵌紧凑 DES）。不新增 guest 代码。
3. **Viewer gate**：`viewer.py` 在既有每秒授权检查旁边轮询 desktop lease（走 API 新端点），仅当 state=human 且 epoch 匹配时转发输入帧；观察流不断。
4. **API**：`GET/POST /v1/studio/assistant/computer/desktop`（state/takeover/return），owner 校验复用 computer owners。
5. **Agent 工具**：`platform_tools_factory` 签名增加 `(handle, sandbox)`；computer 会话注入 `computer_screenshot/click/type/key/scroll`，首次使用自动 agent_acquire，返回结构化错误提示（用户占用时提示等待）。Run 收尾由 sandbox provider destroy 钩子 agent_release。

### 明确不做（本轮）

- 新建 VM 派生、暂停/快照、多观察者、窗口结构化枚举（窗口查看走截图 + 后续浏览器结构化工具）。
