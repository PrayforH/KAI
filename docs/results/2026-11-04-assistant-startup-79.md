# 助理加载与编码协同本地电脑：79 验收

2026-11-04。代码基线 ff383602，本次实现至 9d288d03；隔离分支 fix/local-computer-link-20261104，未修改共享脏工作区。用户后续要求提交 develop，合入以最终远端回读为准。

## 改动和实际效果

主助理历史合并先分页再投影子消息，只读取本页结果；终态优先使用版本化 snapshot。条件请求在正文投影前返回，BFF 压缩保留弱 ETag。浏览器不再启动时回放 23 个历史子 Run；合并历史刷新，过期补充卡不再重复读详情。

所有 Bot 共用该历史路径。Bot 聊天入口用 include_duties=false，跳过职责执行历史读取；完整资料接口保持原行为。打开 Bot 时不再恢复主助理的 Agent 绑定，也不允许晚到的目录结果覆盖当前 Bot。普通任务和项目入口保留原分支。

IM 状态在当前聊天历史就绪后挂载。电脑默认关闭，不恢复旧 localStorage 打开偏好；聊天就绪 1 秒后可静默读取状态/入口，不挂载 noVNC、不连桌面 socket、不申请控制租约。实际点击才打开桌面。

仅给编码协同通过现有配置 API 设置 execution_environment=personal_computer（revision 6→7，状态仍 active）。授权使用配置能力而非 Bot 名字；当前跟随该账号已选中的本地电脑。新 Bot 会话和旧职责会话都做同租户/本人/归属/生命周期校验，旧职责须活跃、无项目与外部托管线程，保留原聊天。每次动作继续核对执行租约和 fencing。没有增加排队、优先级、自动抢占、按 Bot 固定目标或工作目录重构。

## 测量口径

同一现场历史数据、隔离只读组合根：原主助理 24 条消息/332 项总记录、303066 字节，1673/1000/1326 ms；新版首个冷样本 636 ms，随后 170/148/207 ms，303186 字节（额外 runStatus 元数据）。弱 ETag 条件请求 61 ms、304、零正文。这是历史 API 时间，不是浏览器完整首屏或模型首 token 时间。

79 实际 API 的 Bot 聊天入口样本：天气 36/28 ms，编码协同 38/29 ms，休息 37/42 ms；各自历史 78/157/50 ms（154280/235554/10994 字节）。旧详情本身已较快（29–51 ms），本轮跳过扫描是减少重复工作和增长成本，不能宣称该接口有数量级加速。

电脑状态发现接口 13–22 ms；私有 RFB 握手 1025–1041 ms。桌面时间与聊天解耦。

## 真实执行及网页

从主会话来源向编码协同投递一条仅执行 pwd、hostname 的验收消息，原会话 assistant_objective_beeac0752f2a4995afa8750a49930427。最终 Run run_97fe61f01dab46c18b974c08c80db828 成功，sandbox.provisioned.provider=remote-computer，工具输出：

```text
/home/kai/workspace
xiaokai
```

主会话持久历史包含对应 sent 和 received 来源行，received 正文为“工作目录是 `/home/kai/workspace`，主机名是 `xiaokai`。”，同一 handover，无新 Bot/职责/定时任务。第一次验收曾仍走 opensandbox-deferred，暴露旧职责会话兼容缺口；修复后再次真实执行成功，不把第一次当通过。

本机 Chrome 独立窗口打开 79：主会话首次电脑关闭，IM 后续显示；点击电脑出现连接状态。新版编码协同页面已看到上述真实结果，电脑关闭；侧栏切到天气、休息，出现对应会话及输入框。保留用户原窗口/草稿，不发送业务消息。原生浏览器工具中途有一次超时，重建后成功读回编码协同页面；没有用 API 健康检查代替这部分实页证据。未使用性能面板测量完整首屏，也未重新做鼠标/键盘与人工接管全套回归。

截图仅保留在本机私有验收目录，不提交用户聊天截图到公开仓库：/Users/xiaokai/.codex/visualizations/2026/11/04/kai-assistant-startup/coding-local-result.png。

## 验证

- 最新 Assistant/API 回归 242 项通过。
- Computer runtime/desktop/VM/control 单元 43 项通过。
- 前端相关 8 文件、43 项通过；TypeScript 无错误，access.py Pyright 0 错误，Ruff 与 diff check 通过。
- 隔离 PostgreSQL 授权套件新增 6 项旧职责归属用例。全套首轮 29 过、1 失败；发现暂停的旧职责无效 payload 被投影成 ValidationError，改为先拒绝非活跃/无效声明，随后对应旧职责及撤销 7 项全部重测通过。其他 23 项未涉及该失败路径；未宣称最后重跑了全套。
- 扩展执行的 assistant-dup-channels 有一条既有源码字符串守卫失败，其断言文本在基线 ff383602 中也不存在；未改动该实现文件。未宣称全仓全套通过。

## 发布

79 API/Worker：kai/superagent-api:20261104-startup-9d288d03，sha256:c9968b90026d8e9d64116d8ee59ab1a92e107015e424c6c643b381676c3e8463。

79 Web：kai/superagent-web:20261104-startup-41a53afb，sha256:519b4c19b9999d858881f78958b6a7c9a8264f7814759f8b47e457f5a73ed462。

79 Control：kai/computer-control:20261104-bot-computer-9d288d03，sha256:e863566ded1092e7939ad135d66cae5703f194425f757c5f4efcc8c3a3464387。

发布目录 /data/kai-assistant-startup-1fdb9abc，最终 release-complete.json。CAS 防止覆盖并发版本，活跃 Run=0 后停 Worker 二次检查，再滚动 4 个服务；均 healthy，nginx 校验及 reload 成功。schema0065 无迁移。Viewer/飞书/微信容器 ID 未变，远端组件 pin 和本地磁盘/浏览器/文件未改；没有操作 174。最终 compose SHA256：3cd2ad1fe97cfe498c407e257ec6194b10d15b20c4d96022206301bae817570b。

## ACP 与后续接入

32.45 /home/xiaokai/acp-test 的 claude-agent-acp 0.100.0、codex-acp 2.5.0 已实测 initialize 成功，协议1。此次没有创建 ACP 会话或发模型任务；原 smoke 客户端会自动 allow_always，未直接复用于 KAI。KAI 任务身份与机主登录身份不同，目前不能声称编码协同已调用 Claude Code/Codex。固定 broker、项目授权、权限卡、取消恢复和真实合成仓库验收见飞书 11.19；他人电脑的当前运维步骤与后续 C1–C4 计划见 11.18。
