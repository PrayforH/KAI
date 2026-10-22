# 新建 KAI 飞书应用

当前用户确认：暂无可复用应用，准备新建专用 KAI 应用。Web 端通过 KAI 后端集成飞书，用户浏览器无需保持在线。79 的集成进程主动建立长连接，不需要对外开放 Webhook 入站端口。

## 创建与授权

1. 进入 [飞书开发者后台](https://open.feishu.cn/app)，创建「企业自建应用」，应用名称 KAI。
2. 添加机器人能力。应用可用范围先选试点人员。
3. 添加最小应用权限：`im:message.p2p_msg:readonly`（读取用户发给机器人的单聊消息）、`im:message:send_as_bot`（以应用身份发消息）。不要为首版申请群聊全量读取或全员通讯录权限。
4. 在事件与回调中选择「使用长连接接收事件」，订阅 `im.message.receive_v1`。
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
