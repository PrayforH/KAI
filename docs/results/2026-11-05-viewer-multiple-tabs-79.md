# 79 云电脑画面在多个页面间互相阻塞

## 原因与修复

2026-11-05 的故障发生在 Viewer 的连接准入阶段。原实现为每台电脑持有一个全局独占锁：已有页面仍连接时，另一个页面在 WebSocket accept 之前被拒绝，浏览器得到泛化的 HTTP 403。现场登录与电脑绑定查询均为 200；`/computer/status` 显示 `busy: true`、控制状态为 `observe`。因此不能把这次拒绝归因为登录失效或 Debian 系统。

Viewer 改为每个已授权目标独立计数，默认最多 16 个查看连接（可配置 1–32）。准入的检查与预留之间不让出事件循环；退出后释放槽位；仍使用单个 Uvicorn worker。连接保留各自的 SSH/RFB 传输，指向同一持久桌面。容量耗尽时保留拒绝并记录明确的运维原因。

Origin、owner、目标绑定、持续授权复核、人工租约与逐条输入 epoch 校验均保留。多个查看页不创建新桌面，不赋予助理人工控制权。既有的有界传输清理继续生效。

## 验证与发布

- Viewer 与目标选择共 26 项测试通过；覆盖同目标并发、容量上限、释放后重连、其他连接持续可用、目标独立、观察模式输入丢弃、旧 epoch 和授权撤销。修改文件 Ruff 检查及格式检查通过。
- 79 仅更新 `computer-viewer`，镜像 `kai/computer-viewer:20261105-multipreview`，ID `sha256:c78ca06b38e7767a0e4e7f1144c8a0a878d2c74505ced15bf3a99d0cf3c9f541`，95 层，保留 root、init 和 pids_limit 64。无数据库迁移。
- 发布前 Compose SHA-256 为 `3acb00aa3c5a1411e4911bcff26b11b339a7d05ddd8b3d94163287b88ef7e27b`，发布后为 `027e47802c5d65494cf5a1437704a37bb620a75efd32ce988778d5bb3181d499`；切换前再次比较，防止覆盖并行发布。
- API、Worker、Web、Control、HTTPS 与原 VM 容器 ID 均未变化。根盘 inode 仍为 `104203198`。只 reload Nginx，未重启 VM，未修改本地电脑或 174。
- Chrome 真实工时填写会话刷新后打开电脑，观察到当前桌面时钟 `14:01`、浏览器/终端/文件图标，状态为“已连接”“仅观看”，取得控制权按钮可用；未点击接管。服务端同时存在两个 `target=cloud` 的 accepted/open 连接。没有用健康检查代替画面验收。
- 发布证据与私有 Compose 备份保存在 79 的 `/data/kai-viewer-multipreview-20261105/`。此结果不代表整条功能线的全量 CI 已通过。

## Ubuntu 基线建议

下一版优先并行验证 Ubuntu 24.04 LTS、X11 桌面与正确的图形会话总线/AT-SPI。Vercel agent-desktop 的 Linux 安装说明要求 Ubuntu 24.04 或更高版本及图形会话环境；Ubuntu 24.04 的标准安全维护到 2029 年 5 月。24.04 是保守候选，不是最新 LTS，也没有实测依据声称仅换发行版就能缩短截图或模型往返。

新建候选 VM 后验证同机 shell/GUI、原生控件、真实画面、接管交还、文件与浏览器资料迁移，再决定是否切换绑定并保留旧 VM 回退。当前未执行系统迁移。

参考：[agent-desktop 安装说明](https://github.com/vercel-labs/agent-desktop/blob/main/docs/install.md)、[Ubuntu 发布周期](https://ubuntu.com/about/release-cycle)。性能分解和 Jev 思路见 `../plans/2026-11-05-vm-latency-and-jev.md`。
