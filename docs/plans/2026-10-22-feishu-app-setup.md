# 新建 KAI 飞书应用

当前用户确认：暂无可复用应用，准备新建专用 KAI 应用。Web 端通过 KAI 后端集成飞书，用户浏览器无需保持在线。79 的集成进程主动建立长连接，不需要对外开放 Webhook 入站端口。

## 创建与授权

1. 进入 [飞书开发者后台](https://open.feishu.cn/app)，创建「企业自建应用」，应用名称 KAI。
2. 添加机器人能力。应用可用范围先选试点人员。
3. 添加最小应用权限：`im:message.p2p_msg:readonly`（读取用户发给机器人的单聊消息）、`im:message:send_as_bot`（以应用身份发消息）。不要为首版申请群聊全量读取或全员通讯录权限。
4. 在事件与回调中选择「使用长连接接收事件」，订阅 `im.message.receive_v1`；在回调订阅中启用 `card.action.trigger`，同样使用长连接。
5. 保存配置并发布应用版本，由企业管理员批准所需权限和可用范围。
6. 提供 App ID；App Secret 由管理员通过 79 私有配置注入。长连接进程必须已启动，开发者后台才能检测到连接；因此连接验证可与配置发布交替完成。

## 接入边界与验收

- KAI 登录用户生成短期一次性绑定码，发送给机器人后绑定本人。绑定码仅存摘要；不能让消息中的 user_id 指定 KAI 身份。
- 同一应用的多个长连接客户端会分配消息。测试环境使用独立应用，避免分走生产消息。
- 接收回调 3 秒内完成持久入队，模型执行异步处理。
- 收信按 `message_id` 去重。官方特别说明不能依赖 `event_id` 去重。
- 发信带稳定 `uuid`，官方去重窗口只有 1 小时；持久发件箱需限制不确定投递的重试窗口，超过窗口需人工核查，不能宣称永久 exactly-once。
- 首版只向绑定本人发送私聊。文档与日历工具属于另一个授权范围，机器人可发消息不等于可读取个人文档。
- 完整验收：本人绑定 → Web 创建事项 → 执行 → 本人飞书收到结果 → 飞书继续同一事项 → Web 显示变化；另测未绑定用户、重复消息、过期绑定码与解绑。

## 官方依据

- [长连接说明与 Python SDK 示例](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case.md)
- [接收消息事件、权限与去重要求](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive.md)
- [发送消息、应用权限与 uuid 去重窗口](https://open.feishu.cn/document/server-docs/im-v1/message/create.md)

连接未完成前，界面必须显示「待配置」；不得把模拟消息验收当成真实飞书连通验收。

## 第二轮实现与私有配置

已实现持久绑定、收件去重、中文命令、一次性卡片操作、结果发件箱及 Web 连接状态。真实 App 尚未接入，配置前保持停用。

私有配置变量：`HARNESS_FEISHU_ENABLED=true`、`HARNESS_FEISHU_APP_ID`、`HARNESS_FEISHU_APP_SECRET`，以及可选的 `HARNESS_FEISHU_PUBLIC_BASE_URL`。最后一个变量若配置，须为使用者终端可访问并信任证书的 HTTPS 地址，不含路径；未配置时卡片省略网页跳转按钮，长连接和卡片回调仍可使用。凭据注入 API 和专用接收进程；不注入前端构建。79 接收器镜像为 `kai/feishu-receiver:20261022-v1`，入口 `python -m harness.feishu.main`，与 API 共享数据库和应用配置，不开放入站端口。

用户命令：`事项`、`新建 标题`（换行填写要求）、`状态/执行/暂停/恢复/完成/停止 标题`、`补充 标题`（换行填写内容）。新建默认手动执行；检查频率、项目、Agent 在网页设置。状态变更和执行由卡片操作确认；操作令牌绑定本人、事项 revision 和 10 分钟期限。权限敏感工具审批继续在原任务页处理。

绑定码含 96 位随机值，只保存 SHA-256 摘要，10 分钟失效，重新生成会撤销旧码，生成间隔至少 30 秒。解绑停止后续私人投递；已开始的投递完成后解绑才返回。默认重要通知对完全相同的成功摘要做文本级去重，并不宣称能判断语义变化；全部执行结果保留在站内。

接收状态如实区分“进程在线，等待消息验证”和“已收到事件”。投递不确定时使用相同 UUID 在有限窗口内重试；首次尝试时间在网络调用前提交，超过 50 分钟或达到重试上限标记未确认，避免跨过平台去重窗口自动重发。

补充官方依据：[卡片按钮 JSON 2.0](https://open.feishu.cn/document/feishu-cards/card-json-v2-components/interactive-components/button.md)、[卡片回传交互](https://open.feishu.cn/document/feishu-cards/card-callback-communication.md)。

## 2026-10-23 实际应用配置

用户打开的专用 KAI 应用 App ID 为 `cli_aa51ac4376b9dc0e`，属于飞书个人版自建应用。通过开发者后台完成并逐项核对：机器人能力启用；事件和回调均采用长连接；订阅 `im.message.receive_v1` 和新版 `card.action.trigger`；仅开通 `im:message.p2p_msg:readonly` 与 `im:message:send_as_bot`。平台推荐的群聊读取权限未开通。两项最小消息权限和消息事件订阅已获用户明确确认。

已保存机器人入门说明，并发布版本 `1.0.0`（版本记录 `7699772971884694545`）。页面明确显示「已启用」「已发布」「当前修改均已发布」。可用范围仅所有者肖凯；外部群与外部用户访问均关闭。

当前服务器目标已改为热点下的台式机 `172.20.10.7`。应用发布不等于接收器连通：App Secret 尚未注入台式机；现有 KAI 网页为 HTTP `http://172.20.10.7:3501`，还需可信 HTTPS 入口供通知卡片跳转。正在向用户确认凭据保存及现有 HTTPS 配置。未启动此应用的长连接接收器，尚未验收真实绑定、收发与卡片操作。

后续用户确认先走通、换办公网后统一调整配置，并明确授权凭据私有转存和本人账号绑定。已将现有 Secret 写入台式机私有配置（0600）；本机临时文件已删除。按无公网入口模式接通长连接：保留 HTTPS 地址校验，空地址时仅隐藏网页跳转，不提供无效链接。真实绑定、飞书创建事项、卡片触发模型执行、飞书收到结果均已成功；正式本地运行镜像切换后再次执行和通知成功。最终报告见 `docs/assistant-desktop-feishu-20261023.md`。
