# 合并 develop 并部署 79（2026-10-29）

已将 `origin/develop` 的 `4d30bbd4` 合入 `auto/kai-dot-20261028`，部署源码提交为 `8f2c3a8f`。对话组件唯一冲突按两边最终行为合并：保留个人助理结果卡，移除 develop 已移除的分支切换器，纳入新版项目资源、复用、侧栏和响应操作样式。助理页电脑图标与统一移除帮助入口继续保留。

API、Worker、computer-control、Web、飞书及微信六个服务已更新至 `20261029-develop-8f2c3a8f` 镜像，镜像 revision 标签均匹配源码提交。Viewer 保持已验证的 `20261029-v12-clipboard`；VM、根盘、浏览器和工作区未重建。数据库当前 0054，本次没有新增迁移。pypdf 按合并后的锁文件升级为 6.21.4。

前端 166 项、后端 176 项检查通过，TypeScript、Ruff 及 Linux amd64 Next.js 生产构建通过。切换前暂停接收器并复查没有非终态 Run，再切换镜像恢复接收器。API、Worker、Control、Web 健康，飞书及微信接收器在线。

发布后重新通过实际 HTTPS 网关读取本人历史、项目资源及 Computer 状态；10 份既有通道文件的预览/下载均 HTTP 200、SHA256 匹配，匿名访问被拒绝。飞书 7、微信 3 份交付状态仍为 sent，无待发送或失败项。三端既有同会话记录保留。这次未另外向客户端发送新验收消息，不将既有回执说成新一轮收件人确认。

真实浏览器刷新后可见助理历史、结果卡、右侧纯电脑图标，并显示“我的电脑 已连接 观察中”；点击既有 HTML 结果卡，预览中可见正确标题和正文。项目列表及资源读取接口均 HTTP 200。

入口：https://172.20.105.79:3543/assistant 。部署与证据目录 `/data/kai-merge-develop-20261029-8f2c3a8f`。私有配置备份 `/data/kai-79-20261013/compose.private.before-develop-8f2c3a8f.json`；回滚时只恢复对应服务旧镜像并逐服务切换，不降级数据库或删除数据。报告不保存登录口令、token、CDN 引用或私有 compose 内容。

机器可读证据：[2026-10-29-merge-develop-79.json](2026-10-29-merge-develop-79.json)。
