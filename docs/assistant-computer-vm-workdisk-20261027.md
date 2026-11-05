# Computer V1：共享工作盘与受限 SSH 执行入口

2026-10-27，北京时间。接续[独立桌面 VM 验证](assistant-computer-desktop-vm-20261027.md)。本轮新增实际 guest 控制入口、跨内核启动的进程识别，以及 Docker 工作卷与 VM 的双向读写验证。正式 79 仍运行 r21/0051，本人电脑、账号绑定和聊天记录未由本轮迁移。

## 实现

### 持久进程身份包含 kernel boot ID

`supervisor.py` 在 namespace 身份中保存实际 kernel boot ID。跨内核启动时不再拿旧 PID、启动时间或 namespace inode 检查新内核的进程；旧内核的进程不可能存活到新内核。

缺少 boot ID 的旧记录只可用于证明原 namespace 已不存在。如果数字标识仍匹配活跃 namespace，执行入口保持拒绝，要求物理恢复，不为兼容而直接加入该 namespace。升级必须先停止原实例；不能在活动任务中热替换监督器。

### 固定 SSH 协议

新增 [ssh_gate.py](../src/harness/computer/ssh_gate.py)，仅供未来内部节点在完成 canonical 授权后调用：

```json
{"version": 1, "action": "exec", "spec": {"epoch": 1, "lease_id": "...", "argv": ["..."], "cwd": "/home/kai/workspace"}}
```

只允许 init / activate / renew / deactivate / exec，拒绝额外根字段、其他版本及选择 root 程序的请求。输入限制 40 MiB；请求经 root-only 一次性 spool 进入固定监督器。Root Python 使用 `-I`，显式设置 NoNewPrivs，模型命令固定为 UID/GID 28000，无 capabilities。错误不回显输入、环境或私有路径。

[guest-control.sh](../deploy/assistant-79/computer-vm/guest-control.sh) 配置专用 `kai-control` 账号。公钥文件及其目录由 root 持有，公钥包含 restrict 与固定 command；sudo 仅允许固定 Python、固定 `-I` 参数和固定入口。普通任务账号不能修改这个授权关系。客户端私钥始终在 79 私有目录，没有挂入 VM runner 或传给模型。

入口不接受客户端指定的 SSH 地址、身份或 root 脚本，也不自行判断平台用户/Run 授权；当前没有为浏览器或模型开放这个私有协议。

### 工作盘失效时拒绝写入旧 HOME

数据操作打开 `/home/kai` 后，从目录 FD 的 mount ID 找到实际覆盖该目录的挂载，要求 UID/GID 28000、9P `kai_home`、整个导出根目录、rw / nodev / nosuid。只检查路径曾经出现过一个正确 mount 不够，因为它可能被新的空挂载覆盖。

挂载缺失时不回退到 OS 盘里的旧 HOME；deactivate 保持可用，以便停止仍持有旧挂载的后台写入进程。实际应用调用前还必须由节点核对物理实例、卷标签、用户和当前执行权，本轮没有用 mount tag 代替这些校验。

## 79 的实际工作盘验证

原 cloud kernel 没有 9P。本轮安装 Debian 签名的 generic kernel `6.1.0-54-amd64`，实际加载 `/lib/modules/6.1.0-54-amd64/kernel/fs/9p/9p.ko`。新增 [guest-kernel.sh](../deploy/assistant-79/computer-vm/guest-kernel.sh)，从实际安装的 metapackage 确定启动项，避免同 ABI 的 cloud kernel 排序靠前。

使用独立 Docker 测试卷 `kai-computer-workdisk-probe-20261027`，没有挂载本人正式工作卷。先在原 v0h Docker 镜像中由 UID 28000 创建 200 个文件、identity、HOME 可执行脚本与符号链接，停止 writer 后交给 VM。

QEMU 使用 UID/GID 28000、cap_drop ALL、NoNewPrivs、只读 rootfs，采用 `security_model=passthrough,multidevs=forbid`。Guest 挂载使用 `cache=none,nodev,nosuid`。本轮只验证同 UID 的用户工作目录，不声称已实现任意 UID 的透传或系统软件全量迁移。QEMU 对各安全模型的说明见[官方文档](https://www.qemu.org/docs/master/system/qemu-manpage.html)。

| 验证 | 实际结果 |
| --- | --- |
| Docker → VM | 原 200 文件与 HOME 脚本实际读取、执行，原 identity SHA-256 / mtime 不变 |
| VM 写入 | 原子替换新文件，0640 权限、实际符号链接、user xattr 均成功 |
| VM → Docker | 正常关闭 VM 后，原 Docker 镜像读回真实 symlink、xattr、权限，并写回新文件 |
| Docker → 重启 VM | 自动挂载同一卷，通过受限 SSH 执行读回 Docker 后写入的文件 |
| 控制身份 | 实际任务 UID/GID 28000，NoNewPrivs 1、CapEff 0，不能读取浏览器 profile |
| 私有入口 | 任意 SSH Shell 命令被忽略，非法帧返回 125，端口转发被拒绝，私有输入不回显 |
| 挂载丢失 | 新 activate 被拒绝，控制状态哈希不变；重挂后同一 token 可激活 |
| 丢盘时清理 | 先观察活跃 detached writer；卸载全局 HOME 后仍可 deactivate，物理卷上的写入停止 |
| 长命令续租 | 命令运行超过初始 3 秒租期，期间并行 renew 成功，命令完成；renew 约 0.332 秒 |

证据：[原 Docker 文件](results/assistant-computer-vm-workdisk-20261027/before.json)、[VM 首次读写](results/assistant-computer-vm-workdisk-20261027/ssh-write.json)、[Docker 回读与后写](results/assistant-computer-vm-workdisk-20261027/docker-return.json)、[VM 重启后读取](results/assistant-computer-vm-workdisk-20261027/ssh-read.json)、[挂载丢失时清理](results/assistant-computer-vm-workdisk-20261027/missing-disk-cleanup.json)、[并行续租](results/assistant-computer-vm-workdisk-20261027/concurrent-renew.json)。read 阶段未重测的挂载丢失/转发字段为 null。

重启前 boot ID 为 `2181ba44-ad94-4a05-a1ae-4772bede29ae`，重启后为 `f1106478-f923-440b-8c31-e928cd4c3517`。原工作卷 identity 的 SHA-256 始终为 `da706d04fec8c584fc65854e25c91cf4b8c95f43dcb30e55455b39bf2b3d5aae`，mtime 未改。[重启挂载记录](results/assistant-computer-vm-workdisk-20261027/after-reboot.json)。

新内核中重新验收了真实 Chromium：Namespace、PID / Network namespaces、Seccomp-BPF 与 TSYNC 启用，浏览器本地测试数据保留；正常终端执行与回收没有更换浏览器 PID。[桌面结果](results/assistant-computer-vm-workdisk-20261027/desktop-after.json)。没有登录第三方网站或进行 KAI 网页接管验收。

## 检查与范围

- 33 项相关单元测试通过，覆盖 boot 身份、旧记录、协议与实际挂载选择。
- 79 宿主 3.10 的隔离 Docker 与新 guest 6.1 的隔离 PID/mount namespace 中，监督器回归各 11 项通过。实际日志：[3.10](results/assistant-computer-vm-workdisk-20261027/supervisor-kernel-3.10.log)、[6.1](results/assistant-computer-vm-workdisk-20261027/supervisor-kernel-6.1.log)。
- 新监督器还完成真实 VM 的两轮文件/工具复用、SSH 绕过拒绝、后台进程回收与浏览器保留验证。[实测](results/assistant-computer-vm-workdisk-20261027/supervisor-real-vm.json)。
- Ruff 通过，Pyright 0 错误/警告，两个 guest shell 脚本语法与实际执行通过。[检查](results/assistant-computer-vm-workdisk-20261027/checks.json)、[六份源文件核对](results/assistant-computer-vm-workdisk-20261027/source-verification.json)。

本轮手动授予隔离 fixture 的 token，报告明确记录 `canonicalAuthorizationTested: false`。这些结果没有证明真实 Worker 已使用 VM。当前测试盘仍含私有验证账号和测试数据，不能直接当作正式用户镜像。

下一阶段先构建清理过的 guest base：去除测试账号授权、共享 host key 与测试 profile，补齐受支持的软件清单，并为每台电脑生成独立凭据。随后接 VM 生命周期、节点私有凭据存储、现有账号/Run/SandboxLease 授权与 Worker；完成新建、失联、迟到请求、回退及真实模型任务，再迁移本人试点。浏览器账号代理、观察/接管互斥及飞书/微信/网页端到端验收仍待完成，整体编排、记忆与交付继续按[原方案](plans/2026-10-27-assistant-dot-bot-computer.md)推进。

正式 r21 配置与原发布文件一致，schema 0051；本人电脑仍为原 image / provider_ref / volume，正式服务正常。[正式环境核对](results/assistant-computer-vm-workdisk-20261027/production-unchanged.json)。新监督器尚未进入正式镜像。

验证后 VM 正常关机，VM 和回归容器移除；保留隔离 VM 盘、测试工作卷及 79 私有凭据供后续封装。宿主 `/dev/kvm` 权限未改，没有删除正式卷。[清理](results/assistant-computer-vm-workdisk-20261027/cleanup.json)。实际启动参数见[override](results/assistant-computer-vm-workdisk-20261027/compose.override.json)、[运行配置](results/assistant-computer-vm-workdisk-20261027/vm-runtime.json)。
