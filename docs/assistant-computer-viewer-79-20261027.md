# 79 持久桌面人工预览验收（2026-10-27）

后续已增加主题、壁纸、真实应用 Dock 和助理右侧分屏，最新入口与验收见[外观与分屏报告](assistant-computer-theme-split-79-20261027.md)。本页保留第一轮桌面上线记录。

本人账号可在助理 Header 点击“电脑”，或打开 `https://172.20.105.79:3543/assistant/computer`。本次交付是人工操作的持久桌面预览；尚未将生产 Worker 或模型 GUI 工具切换到该 VM，也未实现人工与 Agent 交还控制。

## 实际交互验收

通过本机 Chrome 中的网页桌面实际操作，未用后端命令代替 GUI 验收：

1. 鼠标操作 Openbox 右键菜单，选择 Terminal emulator，成功打开 uxterm。
2. 在终端通过键盘输入 `echo kai79`，看到输出 `kai79`。
3. 刷新外层 KAI 网页，自动重连到同一终端，原输出保留。
4. 刷新后输入 `pwd`，得到 `/var/lib/kai-desktop`，证明键盘仍可操作。
5. 点击 KAI“重连”，再次看到同一终端及上述两条命令输出。
6. 验收后返回“我的助理”，释放测试连接，供用户自行试用。

本轮没有验收中文输入法、跨系统剪贴板、Agent 自动点击或浏览器登录恢复。刷新重连保留的是仍在运行的同一电脑，不代表进程内存快照恢复。

## 用户反馈的终端故障

Openbox 原菜单调用 `x-terminal-emulator`，基础镜像未安装终端包，因此报 No such file or directory。已在原本人预览 VM 中从 Debian 签名软件源安装 `xterm 379-1`，保留原 OS 磁盘、电脑身份、工作盘和浏览器配置，并在桌面启动脚本设置 `SHELL=/bin/bash`。基础镜像安装清单也增加 xterm，避免新电脑复现。

另修复 noVNC npm 入口：固定 `@novnc/novnc` 1.7.0，使用公开 ESM 根入口。此前 1.6 的 npm CommonJS 产物与顶层 await 不兼容，可能出现画面和鼠标正常但键盘没有发出事件的情况。验收使用修正后的 r19 Web。

## 发布内容与保留边界

| 内容 | 79 实际版本 |
| --- | --- |
| Web | `kai/assistant-web:20261027-r19-computer-viewer` |
| Web image ID | `sha256:00ca507ff5714c0bcea49c0c16f08645f800ba5b0237decb18805e52a5e86bfd` |
| Viewer | `kai/computer-viewer:20261027-v2` |
| Viewer image ID | `sha256:6d329343335bad70606d30b353cab86a7a3b634964d896c2d908128ba38dfed8` |
| VM runner | `kai/computer-vm-runner:20261027-v2` |
| Runner image ID | `sha256:f6ff82bbfc167157aca72e14a0812eb8aa2dad47dbe7405d12e9180273be9b70` |
| API / Worker | 保持 r21，image ID `sha256:d310cd7929c2d2b4b4b6cf91bd759eacb66d9f49fce2a584e280770575458934` |
| 新电脑用无凭据基础镜像 | `guest-desktop-v4.qcow2`，20 GiB 逻辑磁盘，无 backing file |
| 基础镜像 SHA-256 | `1188d14f6d0024526c6e7911fc74c5e0a5a554cc4758019c0b10f1d079130e86` |

预览服务已纳入 79 私有 Compose，VM 名称为 `kai-computer-owner-preview-20261027`，标记 preserve。该电脑沿用已启动的预览磁盘，并原地补齐终端；没有用 v4 模板覆盖本人磁盘。v4 仅供后续创建干净电脑，构建器正常退出 0、qemu-img check 通过。

生产 API、Worker、飞书及微信接收器、数据库结构、绑定和聊天记录均保持。本人的原模型执行 Computer 和工作卷仍由原驱动使用；新预览电脑的文件不会自动与其合并。

## 访问与隔离

- KAI 登录态经现有 API 校验本人和租户，浏览器不能选择目标主机、SSH 用户、密钥或命令。
- WebSocket 校验 HTTPS Origin 和 HttpOnly 登录 Cookie；连接期间每秒重验授权，过期或撤销后停止转发。
- 一个预览电脑只允许一个 viewer 连接。页面离开会主动断开，缓存恢复时重新连接。
- Viewer 使用独立 SSH key 和固定 host key，只能访问强制 display gate；控制 key、viewer key 和不同电脑的 key 不能互用。
- VNC 仅监听私有 UNIX socket，经 SSH 和鉴权 WebSocket 转发；未发布原始 VNC、X11 或 CDP 端口。维护 SSH 仅绑定节点 localhost。
- 桌面、任务、控制和查看使用不同 UID；任务身份不能读取浏览器私有目录。Viewer 无 Docker socket 或数据库配置。

实际检查：跨电脑及跨角色密钥被拒绝；viewer 入口忽略任意 shell 命令，只返回 RFB 流；任务身份读取浏览器目录被拒绝；5900、5901、6000、6099、9222 未监听。匿名 status 请求被拒绝。

## 代码验证

- 新增 seed、密钥存储、viewer 相关单元测试 20 项通过，覆盖重试不换身份、损坏/权限异常拒绝、账号/租户/Origin、互斥及授权撤销。
- Ruff、Linux 目标 Pyright、TypeScript 检查通过。
- Linux Next 生产构建及 Shell 语法检查通过。
- 79 上 Web、API、Worker、viewer 和预览 VM 均运行，重启策略为 unless-stopped。

下一步仍按[整体方案](plans/2026-10-27-assistant-dot-bot-computer.md)接 VM Provider、canonical 授权、Worker 和人工/Agent GUI 控制互斥。官方 OpenSandbox SDK 迁移目前是设计方向，没有在本轮替换已有执行协议。
