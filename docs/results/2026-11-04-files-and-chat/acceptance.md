# 飞书历史HTML与聊天固定区域修复

79已发布API／Worker／Web 20261104-files-v1，Control／Viewer维持native-desktop-v2。健康、版本及/assistant HTTP 200通过，证据release.json。提交345a8560；没有重建32.45、云虚机或用户盘，没有修改174或共享main工作区。

## 文件投递

历史调研HTML为AI应用长期记忆接入调研-2026-11.html，37659字节，artifact_1132586d20e044e2bd261f4563f05a90。此前对象存储读回和SHA256核对正常，但后续飞书轮次没有引用该旧artifact，因此根本没有进入文件队列。

现增加assistant_file_delivery通用工具：列本会话历史成果、发送用户指定文件到本人当前飞书绑定、查询真实发送回执。应用配置在服务端解析，模型不需要应用密钥；文件归属、原始会话、当前执行代次与状态、当前账号及飞书绑定均检查。显式发送按原artifact的run_id入队，保持ChannelFiles的加密上传凭据、幂等和重试；同key不能换文件或换绑定，解绑后禁止发送。没有默认转发整个Web历史。

已按用户要求通过发布API容器的正式领域方法queue_artifact补发该文件，既有飞书接收器负责上传和发送，返回sent及真实messageId。没有通过SSH复制进飞书客户端或手写数据库记录冒充发送。feishu-receipt.jsonl保存pending→sent回执；这证明飞书API确认发送，不代表用户已阅读。

## 聊天区域

消息及实时状态通过portal移到Thread.Viewport内的普通消息流，离开固定ViewportFooter输入区。遗留本地待发消息依据已加载的持久事件恢复原run位置；已确认事件消除重复本地记录，不自动重放不确定输入。普通任务／项目队列仍保持独立。助理切换时输入组件按threadId隔离。

去掉固定“查看更早的消息”栏。保留原滚轮／触摸／键盘向历史顶部读取时自动分页和滚动位置保持，不触发无用户阅读意图的全量加载。41项前端测试包括固定输入区不再含消息／进度、遗留气泡回归原turn及历史分页；真实浏览器页面由用户刷新后确认。

## 验证

12项文件通道集成测试、6项附件／工具单元、41项前端通过，TypeScript、新增及直接改动的文件模块Pyright、相关Ruff通过。composition有8项现存类型问题（未改动的类型约束／服务协议等），未声称全仓类型检查无错误。正式补发使用execution_enabled=False的API控制面容器配置，避免误启电脑执行；没有启动Agent或额外模型调用。

Recorder与Replay只加入飞书11.16和docs/plans/2026-11-04-kai-recorder-replay.md。用户优先级已记录：两者都要，先录制人的操作供助理学习复用，再做助理过程回看排障；未实现录制能力。

飞书11.16发布记录与后续计划已回读revision122：https://my.feishu.cn/docx/XV4ndItNmoONoIxQPWgcIyLbnJe#doxcnWt9VbSkmsUYSfZzscSMIbe
