# D3 云电脑切换冒烟：切 cloud → 真实命令 → 切回 local（2026-11-05）

**结果：✅ 通过（产品路径）。** 切机、云上执行、切回全程走产品 API 与真实 Run。

## 链路证据

| 步骤 | 动作 | 结果 |
|---|---|---|
| 切 cloud | `POST /targets/select {"target_id":"cloud"}` | `active_target=cloud`（revision 385）；registry 行 `computer_76649d1de938…` provider 从 remote-computer 变为 **vm-computer / registered**（一人一台：切机=替换底层 computer，opaque target 不变） |
| 云上执行 | 真实 Run `run_f63232578648404ca3ffa180d942598a`（编码协同线程），prompt 查 hostname | run **succeeded**；`tool.request` seq=49 `Bash: hostname; cat /etc/hostname; hostnamectl` → `tool.allowed`（policy production-orchestrator）→ `tool.result` seq=135 `is_error:false`，输出 **`kai-aaaaaaaaaaaaaaaaaaaa`（VM，`Chassis: vm`）**——确认执行发生在云 VM 而非本机 |
| 租约 | execution leases | `computer_lease_0343b899…(start)` + `36834a60…(execute)`，21:50:54→21:50:55 后 released |
| 切回 local | `POST /targets/select {"target_id":"local-32-45"}` | `active_target=local-32-45`（revision 391）；registry 回 **remote-computer / registered** epoch=99 |

## 观察到的语义（演示可讲）

- **一人一台、opaque target**：切机不改 target_id 列表，只替换底层 computer（provider 都能换）。
- **idle-only 切换**：切换在无活跃 Run 时瞬时完成；活跃 Run 期间切换被 409（D2 已实证 409 分支）。
- **租约成对**：每次云上执行 start+execute 两条 lease，结束后 released。
- 云 VM 的 hostname `kai-aaaa…` 是 guest 预置机名；`computer-desktop` VM 容器 Up 7 days。

## 遗留（不影响 D3 判定）

- viewer 桌面画面（cloud VM 的 VNC）未在本环打开核验——D3 只验执行链路；画面核验属 D4 演示彩排。
- 互切期间 policy 事件（`tool.allowed` production-orchestrator）说明云上 Bash 走的是沙箱策略而非电脑直控——与产品实现一致（云电脑执行=沙箱内 Bash），演示话术注意区分「电脑工具」与「computer_acp（Claude/Codex）」。
