# 台式机 KAI 助理与飞书接入验收

日期：2026-10-23（Asia/Shanghai）。应用目录 `/home/xiaokai/deployments/kai-assistant-20261023`，Compose 项目 `kai-assistant-desktop`，正式配置 `private/compose.json`。目标机原 KAI 工作区和其他应用未覆盖。

访问：`http://172.20.10.7:3501/assistant`（直接持续对话），辅助管理页 `/studio/assistant`。账号为 `xiaokai@kai.local`；随机密码仅保存在目标 `private/login.json` 与本机私有凭据文件，不进 Git。公开注册已关闭。数据库迁移 0045，PostgreSQL/Redis/API/Web 健康，Worker 与专用飞书接收器运行；数据服务使用独立卷。

## 最终镜像

| 服务 | 镜像 | 镜像 ID |
| --- | --- | --- |
| API / Worker | `kai/assistant-desktop-api:20261023-r3` | `a92cbf776b9e59df3e095ba2bbe0e46955147dba3384786ef71bfecfa30a940b` |
| 飞书接收器 | `kai/assistant-desktop-feishu:20261023-r4` | `11baeca8b03a5f1e005e4fe2d45b8f219feb53aa52e451b307bc802e4c4cb5f5` |
| Web | `kai/assistant-desktop-web:20261023-r2` | `81ead6f35df1f762e5a77cd86f7ba273527107650a7ef2165bf035970fbc662b` |

最终使用正式 `local-runtime` 阶段，已替换早先用于验证的 builder 镜像。它包含锁定的 Python 依赖、Node、办公文档依赖及 Codex CLI。当前仅开启 Claude Agent SDK / DeepAgents、本地容器内执行；不部署 Kubernetes、不含 kubectl、不挂载主机工作区或 Docker socket。进程 UID 10001。默认通用 `runtime` 阶段仍包含经校验和补丁构建的 kubectl；台式机无需因其 GitHub 下载超时阻塞本地运行。

## 真实验收

- 网页：真实登录与模型回复；刷新恢复历史；追问返回前一条随机验收代号；再次刷新保留回答。证据 `results/assistant-desktop-20261023/browser.json` 与 `chat-refresh.png`，该截图来自首次成功验收，早于后续侧栏样式修正及正式镜像切换。
- 飞书应用 `cli_aa51ac4376b9dc0e` 已发布 1.0.0；机器人、长连接消息事件 `im.message.receive_v1`、新版回调 `card.action.trigger` 已启用。权限仅 `im:message.p2p_msg:readonly`、`im:message:send_as_bot`；可用范围仅所有者，未开放外部共享。
- 官方接口确认应用凭据有效；飞书本人发送“连接测试”后收到真实绑定提示。经用户确认，将飞书肖凯绑定到本机 KAI 小凯账号。绑定及通知设置在正式镜像切换后保留。
- 飞书创建验收事项，点击真实卡片“执行一次”；执行 `run_3cd20acb726840bbaf304b8248714d49` 成功，结果“验收成功”已在飞书显示，同一事项在 KAI 持久记录。测试事项随后标记完成。证据 `results/assistant-desktop-20261023/feishu.json`。
- 正式镜像切换后，从 KAI API 创建并执行第二条验收，`run_d8b69b14e8a9490ba33b19c5aca64c02` 成功，结果“正式镜像验收成功。”再次送达飞书。接收器重启后，真实查询和“暂停”卡片再次成功，KAI 状态同步为 paused；测试事项随后标记完成。证据 `results/assistant-desktop-20261023/runtime.json`。个人对话历史也在切换后恢复，保留随机代号和回复，共 9 条消息；见 `history-after-cutover.json`。
- 无 HTTPS 入口时省略“在 KAI 查看”按钮，保留卡片回调。地址一旦配置仍严格要求 HTTPS；不通过伪造 URL 或忽略证书验证绕过。18 项飞书单元检查覆盖凭据要求、HTTPS 校验、无入口回调卡片以及 SDK/协议契约；另有本轮前端 68 项布局/侧栏检查及 Linux 构建通过。

## 使用范围与后续

网页助理可持续聊天；飞书首版目前支持显式事项命令：`事项`、`新建 标题`、`状态/执行/暂停/恢复/完成/停止 标题`、`补充 标题`，新建默认手动执行。自然语言驱动持续事项管理尚需接入，不把命令入口描述成完整的自由对话 Agent。

换网后确认台式机新 IP 和出网能力；数据库、绑定和 App Secret 无需重建。长连接只需主动出网，无需公网 Webhook。后续接入稳定可信 HTTPS 时设置 `HARNESS_FEISHU_PUBLIC_BASE_URL` 并重建应用进程配置，网页跳转按钮恢复。只运行此应用的一个接收器，避免其他环境分走消息。

私有 Compose 和数据库备份保存在目标 `private/`，切换前配置快照文件名带 `before-feishu` / `before-local-runtime`。接收器无需入站端口。凭据不得复制到本报告、前端构建或日志中。
