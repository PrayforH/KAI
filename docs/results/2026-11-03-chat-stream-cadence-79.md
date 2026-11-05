# 助理流式消息误拆与任务周期工具（79）

2026-11-03；代码9354fdff，独立kai-weather-fix工作树，分支feat/kai-assistant-routing-20261103。用户指出“先”被单独显示，并要求一起修复无法改周期；补充明确重建也可以，问题是主助理缺创建任务工具。

## 原因与修复

真实截图轮次run_17945b4cea3a44999b3fafddd483f797的事件791为“先”，794、795为后半句，均属于同一个message_id（尾缀c844a4cfac514c14b6656f9524b94c9b），792插入assistant_update_task工具事件。assistantChatReplies却把任何工具事件都作为气泡分界，造成“先”永久单独成条；不是模型真发了一个完整的单字消息，也不是首token必须延迟。

现在优先采用原消息身份与显式start/completed边界，同一消息被工具回调穿插时仍原位增长、气泡key和原时间不变。实际完整的多条进展仍分开；无编号的旧事件保留工具边界兼容；其他消息延迟到达的completed不关闭当前消息。首token仍立即显示；不依赖字数、标点或定时猜分句，不隐藏内容。仅助理聊天投影修改，普通任务/项目的执行过程和最终答复规则保留；不改历史数据。

周期底层已有PATCH与仓储更新能力，但模型工具assistant_update_task只允许pause/resume/complete/stop，聊天装配又过滤了assistant_create_task，导致模型先停用旧任务后调用不存在的创建工具。现恢复主助理的显式后台创建工具，普通领域请求仍用已有Bot消息派发，不重复造一条任务。明确重建时先确认创建成功再停用旧任务；创建保留原幂等机制。

assistant_update_task新增reschedule及interval_minutes（15–525600整数分钟，null仅关闭周期）和可选timezone。状态查询增加现有周期与下次检查时间。聊天工具及既有PATCH共享AssistantService.reschedule，复用原仓储revision锁、所有权和授权校验；只替换schedule，保留原任务身份、说明、Agent/项目/绑定、创建时间与历史。下次检查从修改成功起计算；当前执行不取消、暂停任务不自动恢复、终态与过期revision拒绝写入。未新增调度器、数据表或迁移。

## 检查

后端257项通过，包括周期原地修改、实际PostgreSQL仓储原历史与执行快照保留、暂停状态、终态/跨账号/租户/过期revision拒绝，以及真实AG-UI路由回归；1项既有Starlette弃用警告。测试仅使用本机专用kai_period_chat_1103数据库，不连接生产写测试数据。前端8文件52项通过，含工具穿插首字回放、真实消息边界、缺编号兼容、延迟completed和任务/项目隔离；TypeScript、Ruff、diff check通过。

## 验收前基线

本人Codex重置盯梢objective_97d50a2472c141e589fdf1f31b7ecff3：active，revision3，每120分钟，原创建时间2026-10-30T09:56:28.555802Z，nextCheckAt=2026-11-03T10:05:13.317080Z。15条职责。排除schedule/revision/nextCheckAt/updatedAt后的原任务载荷SHA256=8b80adcedf86d5d1aaccd4ff26298460d2dc05a6787ca84a8d1f51b29e7f6552，48条原执行均succeeded。

## 发布与实页结果

最终API/Worker/Web均为kai/superagent-{api,web}:20261103-chat-cadence-9354fdff。API image sha256:719de35bad2093df2dcc85aa97abd1bc650da4e890224c72e1a8f05be3b0d9b3，93层；Web image sha256:1c5ac329f9b135135962971a762a0a30ab6b12b71ab27e3c7394db76753b7b5f，9层。3服务healthy，19个累计Python实际安装包hash匹配；compose SHA256=6e8f5dfc9f80978933ba1bbdd286a2e491c1a22b87d587993c1f44906155c1d9。发布前空闲及compose CAS检查、备份/data/kai-chat-cadence-9354fdff（0700、compose备份0600）；schema0065无迁移，不改数据卷/绑定/VM，不动174。

正常登录Chrome新页面核对原截图：原来的“先”已与“停用旧的那条盯梢，保留它的历史记录，再按4小时间隔重建一条。”同一文本节点、同一组复制/回复控件；前后两条实际完整进展仍独立，原始事件没有改写。

本人主助理实际发送“把Codex重置盯梢改为每4小时检查一次，直接修改原任务周期，保留历史，不要重建或停用任务。”run_f4b5e6c231234771977b348799ae2805 succeeded。模型先错误调用resume+interval_minutes，被校验拒绝，没有发生生命周期写入；随后用reschedule+240+expected_revision3成功。原objective_id保留、revision4、active、schedule.intervalMinutes240，updatedAt=2026-11-03T08:17:16.982299Z，nextCheckAt=2026-11-03T12:17:16.982299Z（北京时间20:17）。任务不变量载荷SHA256仍8b80adcedf86d5d1aaccd4ff26298460d2dc05a6787ca84a8d1f51b29e7f6552；48个原执行ID/状态逐项相等，职责总数仍15。实页显示成功与下次时间，未先停用或重建。实际runtime.system初始化目录确认assistant_create_task与assistant_update_task均可用；创建工具幂等与真实仓储由本机测试验证，生产没有额外创建测试职责。

发布切换期间，用户新发“代码连一下 github 仓库”一度显示请求未完整结束；首次只读查询没有该输入的Run或投递记录，未自动重放，已明确告知用户。后续用户重发，实页正常进入仓库信息澄清卡。发布前的空闲检查不能消除检查后到服务替换间新请求到达的竞态；本轮未改成无损滚动发布，不把该次提示谎称为已执行或任务成果，也未改写旧失败回复。

保留：模型周期修改成功后仍输出旧工具能力的过时解释和内部字段；本轮验证真实修改结果，不宣称所有Bot文案已完全收敛。IM、旧09:00固定时刻偏移及天气时效校验不在本轮完成范围。

飞书设计文档已追加11.11，revision96，局部回读“周期与创建缺口”正文确认保存。后续协作沿此版本，不恢复旧ZCode队列。
