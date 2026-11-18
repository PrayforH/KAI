# 116 助理部署

`compose.asr.yaml` 使用小树 2.0 的 Qwen 批量识别服务。`compose.computer.yaml` 为已登记的机主声明一台 4 GiB VM，QEMU 容器上限为 6 GiB。Control 和 Viewer 走同一 Compose 内网。

`compose.web-tools.yaml` 为通用助手启用豆包搜索。API 和 Worker 的私有配置提供 `HARNESS_WEB_DOUBAO_API_KEY`。

在 116 的发布目录合并私有配置与本目录的覆盖文件。

```sh
docker compose --env-file computer/compose.env \
  -f compose.private.json -f computer/compose.asr.yaml \
  -f computer/compose.computer.yaml -f computer/compose.web-tools.yaml config --quiet
```

节点私有的 `compose.env` 指向已核对摘要的基础盘和 Runner 镜像，以及机主工作卷。`application.env`、`control.env` 和 `viewer.json` 保存同一个机主的登记信息、控制端配置和固定 SSH 身份。

从封存基础盘创建独立系统盘和首启 seed。旧版基础盘启动后，先用 `scripts/prepare_bot_guest_update.py` 更新桌面门禁并初始化该机主的 Bot 工作盘，再启用桌面入口。

已有 VM 启用按需桌面前，使用维护 seed 的 `--lazy-desktops` 更新门禁和清理旧的桌面自启。Shell 先准备 Bot 工作目录，侧栏或桌面工具再启动桌面。Worker 在职责到期前 90 秒预热已停机的 VM，职责自身仍由原调度器到点派发。

一台账号 VM 在电脑操作结束后保留 30 分钟，空闲超时再停机。主助理和子助理的直接对话在准备执行时并行唤醒已停机的云电脑，默认使用现有账号工作盘，首个电脑工具照常取得执行租约。直接发纯文本消息也可能触发预热。通过关闭 Worker 的 `HARNESS_ASSISTANT_COMPUTER_VM_INTERACTIVE_PREWARM` 可以恢复按工具首次使用唤醒。

将 `desktop-location.conf` 合入 HTTPS 的 `server`，使浏览器的桌面 WebSocket 使用站点已有的登录和证书。
