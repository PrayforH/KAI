# computer_acp DEMO 可写（2026-11-05）

用户确认 `computer_acp` 当前只读，要求演示期放开可写。本片把它改为 **DEMO 可写**：
**去掉强制只读 mode + 权限请求单次放行（allow_once）**。

## 改了什么（HEAD `03db5621`，分支 `fix/local-computer-link-20261104`）

1. **`acp_client.mjs`**（跑在机主电脑 gate 里的 ACP 客户端）：
   - 删掉强制的 `session/set_mode`（claude→plan / codex→read-only 的写死 modeId）。
     为什么不猜新 modeId：本机已装 `claude-agent-acp` / `codex-acp` 的实际可写 mode id
     无法在写代码时验证（192.168.32.45 当天 SSH 不通），marker 明确「没有把握就去掉
     强制 set_mode」。现在各 agent 用机主自己登录态的默认模式，结果里新增
     `mode: session.mode?.current ?? session.currentModeId ?? null` **如实记录**实际 mode。
   - `session/request_permission` 从一律 `cancelled` 改为 **`allow_once`**（优先级：
     `options[].kind=="allow_once"` → `outcome=="allow_once"` → `allow_selected` →
     `allow_always` 作为兜底 optionId 来源，但 outcome 永远是 `allow_once`）。
     **绝不 `allow_always`，不 hang，不落盘任何永久授权。**
   - 结果字段：`read_only: true` → `demo_writable: true`（旧的只读标记不再存在）。
2. **`acp_tools.py`**：工具 description 与 instructions 去掉「仅只读调研 / 权限请求拒绝」，
   改为「【DEMO 可写】权限请求按 allow_once 放行（仅本次，不记忆）」。
3. **`acp_bridge.py`**：模块 docstring 标注 DEMO（2026-11-05）与回滚方式。

## 测试

`tests/unit/sandbox/test_acp_connector.py` 新增 2 项（共 19 过）：
- demo 文案必须含 `DEMO 可写`/`allow_once`，且**不得**再含「只读」；
- 发到 79/本机 gate 的 `acp_client.mjs` 不得含 `session/set_mode`、不得有一律
  `cancelled` 的 outcome，allow_always 只能作为兜底 optionId 来源而非 outcome。
`tests/unit/sandbox/` 全组 409 过。

## 79 发布

- 镜像：`kai/computer-control:20261104-acp-demo-03db5621`
  （叠加自线上 `kai/computer-control:20261104-acp-3c6a175f`，仅覆盖
  `acp_client.mjs` / `acp_tools.py` / `acp_bridge.py` 三个文件——api/worker 不含这三处文案，无需动）。
- 发布目录 `/data/kai-79-20261013-acp-demo-03db5621`（compose.release.json=回滚快照 + overlay）。
- 闸门：CAS（live==快照）、活跃 Run=0/职责执行=0、schema 0065 未动；只 `up -d computer-control`，
  api/worker/web/feishu/weixin 全部未动。
- 顺带修复：computer-control 容器此前 unhealthy（`BlockingIOError` 进程耗尽卡死），已重启恢复 healthy。
- 冒烟：运行中容器内 `acp_tools` 源码断言「无只读 + 有 DEMO 可写」通过；镜像内 `acp_client.mjs`
  含 `allow_once`×6、`set_mode` 仅存在于注释；`:3543/assistant` 与 `:8080/healthz` 均 200。

## 本机 gate（未完成，阻塞中）

**ACP 实际在机主电脑的 gate 里跑**，必须把新版 `acp_client.mjs` 放到
`/opt/kai-computer/acp_client.mjs` 才真正生效。但 192.168.32.45 本轮 SSH/ICMP 均不可达
（直连 22 关闭；只能经隧道确认机器活着——viewer gate 能执行 python）。**本机 gate 尚未刷新**。
等机器网络恢复后执行（包已备好：`/data/kai-79-20261013-acp-demo-03db5621/overlay/src/harness/computer/acp_client.mjs`）：
```bash
scp root@172.20.105.79:/data/kai-79-20261013-acp-demo-03db5621/overlay/src/harness/computer/acp_client.mjs /tmp/
# 然后在 32.45 上：
sudo install -m 644 -o root -g root /tmp/acp_client.mjs /opt/kai-computer/acp_client.mjs
```
`acp_client.mjs` **不在** gate digest 组件内（digest 只算 remote_display_gate/remote_gate/
ssh_gate/supervisor[/native_desktop]），所以替换它不动 `/etc/kai-computer-remote.json` 的 image 字段。

## 已知阻塞（如实验证）

- **半真实调用未能通过**：control 侧经隧道对 control gate 发 identity 请求返回
  "Computer remote control refused the request."（exit 125）。viewer 侧 gate 能在机器上执行
  python（机器活着）。refuse 发生在 gate 的 `configuration()`——最可能是
  `/etc/kai-computer-remote.json` 的 `image` 摘要与机内已装 gate 文件不符
  （今日实测机内四文件摘要 `29e2e2a2…`，而配置写 `dac63fd8…`；DB 里 control 期望
  `3434b9f0…`）。**机器直连 22 恢复前无法修配置**，故真实 ACP 调用未验证——不假报成功。
- 上午那次直接 SSH 曾成功读到 `/etc/kai-computer-remote.json`（`acp: true` 已配置，
  `native_desktop: true`），配置本身没问题，问题只在摘要字段。

## 如何回滚到只读

```bash
# 79：容器镜像回退（一条命令）
ssh root@172.20.105.79
cd /data/kai-79-20261013-acp-demo-03db5621 && docker compose --project-directory /data/kai-79-20261013 -f compose.release.json up -d --no-deps --pull never computer-control
# 或直接把 compose.private.json 里 computer-control 镜像改回 kai/computer-control:20261104-acp-3c6a175f 后 up -d
# 机主电脑：把 /opt/kai-computer/acp_client.mjs 换回 git 历史里的只读版（5acf4458 的文件）
```
权限恢复只读 = `acp_client.mjs` 里 `request_permission` 改回 `{"outcome":{"outcome":"cancelled"}}`
+ 恢复强制 `session/set_mode`（都在 5acf4458 版本里）。
