# 私有桌面 VM 能力验证

这是独立验证环境，不是正式 Computer provider 或用户桌面入口。79 的正式 r21 服务、本人工作卷与账号配置不挂入该 VM。实际验收与剩余接入要求见 [报告](../../../docs/assistant-computer-desktop-vm-20261027.md)。

## 输入与构建

- 只在有 KVM 的 Linux 主机执行；根目录由管理员创建为 `0700`。本次为 `/data/kai-computer-vm-20261027`。
- 使用 Debian 官方 Bookworm `genericcloud-amd64.qcow2`，下载后先核对发行方 SHA-512；本轮的实际值和来源在报告中。不要把将来变化的 `latest` 当成已验证的同一镜像。
- `debian.qcow2` 为只读 base，`vm/root.qcow2` 为独立 20 GiB overlay，backing 路径固定为容器内 `/base/debian.qcow2`。更换 QEMU runner 时保持原 base 和 overlay，不重建用户盘，也不替换 backing 文件。
- 通过 NoCloud `CIDATA` ISO 注入专用管理员公钥和预先生成的主机密钥。账号固定为 `kai-admin`、UID 28000 `kai`、UID 28001 `kai-desktop`，hostname 为 `kai-desktop-probe`。私钥及 seed 文件不得进入仓库或构建上下文。
- seed 使用只读 **virtio block**，本轮 IDE CD-ROM 没有被 cloud kernel 发现；随后 `/dev/vdb` 的 NoCloud 初始化成功。

构建上下文仅放本目录源码。默认使用 79 已验证的 Debian 12 + CA base，image ID 为 `sha256:64d91f7b885eed272bba87909446b12ff408d4aaa5f1a0e9ca787bbea1a020b9`。其他环境显式指定包含 CA bundle、已核对 image ID 的 Debian 12 base；裸 slim 镜像缺少 CA 时不能直接使用 HTTPS apt：

```sh
docker build --build-arg BASE_IMAGE=<verified-local-base> -t kai/computer-vm-runner:probe .
```

运行器默认 UID/GID 28010，无需启动后再降权。宿主 `/dev/kvm` 的原权限保持。管理员在私有验证目录创建同一 `st_rdev` 的设备 inode `kvm-device`，属主 `28010:28010`、模式 `0600`，仅将该设备按 `rw` 映射到容器 `/dev/kvm`。设备类型、设备号、属主必须先核对，不能接受任务指定的任意设备路径。

`vm/` 及两个磁盘文件必须由 28010 可读写；seed 为只读。管理员 SSH 私钥保存在 `private/`，不挂入 runner。SSH 固定主机公钥，使用 `StrictHostKeyChecking=yes`。唯一 Docker 映射端口为 `127.0.0.1:22079`，不公开 VNC、X11 或 CDP。

```sh
export KAI_VM_PROBE_DIR=/data/kai-computer-vm-20261027
export KAI_VM_PROBE_IMAGE=<verified-immutable-runner-image-id>
docker compose -p kai-computer-vm-probe-20261027 -f compose.probe.yaml config --quiet
docker compose -p kai-computer-vm-probe-20261027 -f compose.probe.yaml up -d --pull never
```

## Guest 验证

首次登录成功后，通过固定公钥的 SSH 在 guest 以管理员运行 `guest-desktop.sh`。它限制 SSH 仅允许 `kai-admin`，普通任务账号不能自行写 authorized_keys 后绕开 Run 的进程隔离。桌面使用独立账号和私有 Xauthority、profile、VNC 密码；Chromium 保持自身沙箱。

1. 在 guest 运行 `scripts/probe_computer_desktop_guest.py --phase before`：真实读取 `chrome://sandbox`、进程权限、任务账号隔离；在真实桌面输入公开测试代号，再通过剪贴板读回并截图。
2. 使用原有受验证的 `supervisor.py`，在 guest 运行 `scripts/probe_computer_vm_execution.py`：两轮实际 UID 28000 执行、文件与 HOME 工具复用、后台子进程回收、旧令牌拒绝、真实 SSH 绕过尝试，以及浏览器不被 Run 回收。它明确不测试数据库 canonical 授权，也不模拟模型成功。
3. 从 guest 正常关机，确认 QEMU 退出 0。使用原盘更换 runner 容器，或重新启动同一 VM；不要复用内存或重新创建测试代号。
4. 运行 `--phase after`，它只读取浏览器保存的数据。再运行终端验证并比较重启前后的文件 SHA-256、mtime 和工具模式。
5. 验收完成后从 guest 关机，确认容器退出，移除本项目容器；保留私有磁盘及运行器镜像以供后续验证。不得删除正式本人 Computer 的卷。

本轮 VNC 只用于私有能力验证，没有 KAI 账号代理、观察/接管互斥、受控 Agent GUI 工具或浏览器账号登录验收；这些接通前不得开放为正式桌面。
