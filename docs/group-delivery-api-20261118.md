# 群广播、转交批次与产物接口

基线：`release/group-qi-progress-20261117` / `0479455623e9b01b6bc3974bce6f777b9cc6f2b6`。仅后端；不含转交卡片和 @ 候选菜单的前端实现。

## 默认口径与运行预算

`@所有人` / `@everyone`（大小写、全角字符、@ 后空格、中文词内空格）通知本群 active 席位中的主助理和 READY/ACTIVE 子助理；PAUSED/BLOCKED/ARCHIVED 等状态不会运行，回执说明原因。重复 token 和与具体成员混用不会重复派发。广播 token 从运行输入剥离，用户原文不改。

每条最多 **16 个初始 run，含主助理**。超过 16 整批拒绝，无截断；没有活跃子助理则只给主助理，并提示退化。主助理被移出时不偷偷恢复席位：只给活跃子助理，提示不能自动汇总；无人可接话时明确通知。

主助理先确认收到，平台等所有初始 run 终止后，再起 **至多 1 个汇总 run**，因此广播最多 17 个 run。取消中不视为已停止；运行/结果读取异常为 unknown，不伪装成功或失败。24 小时仍有未终止/未确认项时，把这些状态交给主助理如实汇总。每份成员结果最多带 2000 字进入汇总输入，原消息正文不合并、不复制存储。广播和汇总的空正文不会因读取时间线而重做原任务。

汇总用现有 worker 的 assistant maintenance 执行，无新常驻任务/内存缓存。一次读最多 25 个批次，扫描 2 秒，每个处理最多 5 秒；任务全部 join/cancel。待处理项轮转更新时间，已明确不可汇总则置 unavailable 并提示。传输异常保留 pending，下轮以同一消息/run 请求键恢复。

## A. 广播发送（扩展现有接口）

`POST /v1/studio/assistant/groups/{groupId}/messages`

```json
{"text":"@所有人 请分别给出发布风险","clientMessageId":"client-request-001"}
```

`clientMessageId` 是一次用户意图的重试键；同一键并发或在派发后事务回滚再重试，不重复起 run。省略键的两次 HTTP 发送是两个用户意图。花名册检查、消息、批次及回执使用现有跨进程群锁，和成员变更串行化；不等待模型结果。

响应仍为 `GroupPostResult`：`message`、`replies`、`notices`。增量消息字段：`deliveryBatchId`、`deliveryMemberId`、`deliveryOrder`；无值时省略。系统回执区分接收数、明确拒绝数、未确认数。发给禁用或成员状态未确认者的前置拒绝也保留到批次 `notices`。

```json
{
  "message":{"messageId":"gmsg_source","deliveryBatchId":"gdelivery_…"},
  "replies":[{"messageId":"gmsg_broadcast_…","author":"specialist","specialistId":"specialist_pm","runId":"run_…","status":"running","deliveryBatchId":"gdelivery_…","deliveryMemberId":"specialist_pm","deliveryOrder":1}],
  "notices":["@所有人：已接收 2/3，拒绝 1，未确认 0。"]
}
```

上例省略了既有 `groupId/authorName/text/createdAt` 等字段。客户端重试可能读到更新的运行状态，持久化回执保留在 `replies` 中。

## B. 转交批次只读接口

`GET /v1/studio/assistant/groups/{groupId}/messages/{triggerMessageId}/deliveries`

同一群、同一原用户消息稳定得到同一 `batchId`，主助理多次转交按实际准入顺序分配 `order`。沿用原群消息、`replyToMessageId`、原 run 和 `ui.assistant_delivery` 关联，不改变结果正文。现有交付事件新增 `deliveryBatchId/targetMemberId/targetOrder`。转交也最多 16 个目标。

```json
{
  "batchId":"gdelivery_…",
  "groupId":"group_…",
  "triggerMessageId":"gmsg_source",
  "kind":"delegate",
  "header":"已转交 项目助理、架构助理",
  "targets":[{
    "member":{"memberId":"specialist_pm","kind":"specialist","name":"项目助理","specialistId":"specialist_pm","status":"active","joinedAt":"2026-11-18T07:00:00Z"},
    "order":0,"messageId":"gmsg_delegate_…","runId":"run_…",
    "state":"succeeded","runStatus":"succeeded","detail":null,
    "result":{"messageId":"gmsg_delegate_…","groupId":"group_…","author":"specialist","authorName":"项目助理","specialistId":"specialist_pm","text":"检查完成","runId":"run_…","status":"completed","createdAt":"2026-11-18T07:00:00Z","deliveryBatchId":"gdelivery_…","deliveryMemberId":"specialist_pm","deliveryOrder":0}
  }],
  "notices":[],"summaryState":"not_required","summaryMessageId":null,"summary":null,
  "createdAt":"2026-11-18T07:00:00Z"
}
```

`targets.state`：`pending/running/waiting/succeeded/failed/stopped/unknown`；原始 `runStatus` 单独返回，未确认时不凭旧占位推断运行失败。`summaryState`：`not_required/pending/queued/unavailable`；广播的 `summary` 是动态读取的汇总消息（包含其运行状态），`queued` 只说明已提交汇总，不意味着完成。

前端卡片至少使用：`batchId/header/triggerMessageId`，以及每个目标的 `member/order/state/runStatus/detail/messageId/runId/result`；汇总用 `summaryMessageId/summary`。群时间线对新批次的用户源消息、主助理卡片头和子助理结果补 `deliveryBatchId`，目标另带成员和顺序。没有批次是 404；非成员是 403；读取不触发派发。

历史批次不回填；旧客户端忽略新增字段，旧正文仍可读取。

确认丢失恢复：`messageId` 同时持久化了稳定请求键 `group-{messageId}`。批次读取及同一 `clientMessageId` 重放会先读既有回复；缺回复且准入为 unknown 时，按该请求键、服务端 group/member/trigger 上下文及实际 session owner 查回既有 run，幂等补回原回复占位，不创建或重发 run。最多读取两个匹配；零匹配、多个匹配或查询异常都保留 unknown，不猜测拒绝或成功。准入快照不覆盖原回执，实时状态以读取结果为准。

同一 POST 重放保留 `deliveryBatchId/deliveryMemberId/deliveryOrder`，与时间线复用同一装饰实现。汇总消息行缺失/暂不可读或事件水合异常时，仍返回其他目标，`summary.status=unknown`；明确权限拒绝仍抛出权限错误。

后台汇总每轮最多扫描 25 批；在独立短事务中先更新扫描时间，再进行有界派发。后续异常、回滚或取消不撤销轮转，持续异常批次不会占住后续健康批次。使用行锁及 skip-locked，不新增进程缓存或游离任务。

## C. 群产物只读接口

`GET /v1/studio/assistant/groups/{groupId}/artifacts?since=2026-11-01T00:00:00Z&until=2026-12-01T00:00:00Z&limit=50&offset=0`

`since/until` 可省略，必须带时区，区间 `[since,until)`。`limit` 默认 50、最大 100，`offset` 非负且不超 PostgreSQL bigint。按产生时间降序、artifactId 升序返回；继续使用 `nextOffset`。这是 offset 分页，持续生成文件时跨页不保证快照一致性，客户端可用固定 `until` 限定本轮范围。

```json
{
  "items":[{
    "artifactId":"artifact_…","name":"方案.pdf","contentType":"application/pdf","sizeBytes":2048,
    "createdAt":"2026-11-18T07:05:00Z","memberId":"specialist_pm","producerName":"项目助理",
    "runId":"run_…","downloadUrl":"/v1/artifacts/artifact_…/content"
  }],
  "nextOffset":50
}
```

只返回 READY 产物，复用既有 artifact 行与文件。鉴权先验证本群成员：**本版人类群成员仅支持创建者**，必须实际匹配 creatorUserId，不以租户身份、群/产物存在或前端合成头像替代鉴权。群外/跨用户/跨租户都是 403。再以 run 的服务端 group_context 与实际 session owner 双重限制范围；个人会话文件排除，被移出的子助理历史产物仍可读，空回复重试前旧 run 的产物也保留。

`createdAt` 取 `artifact.ready` 事件时间；历史文件如果没有事件，返回 null（未知），不编造生成时间；指定时间区间时这类记录不命中。无 object_key、宿主路径或文件副本。下载仍走既有按运行权限检查的接口；前端按当前 BFF 约定映射 `/v1`。

产生者名称只查询本页最多 100 个 memberId，包含已移除的历史产生者；既有成员鉴权入口仍会加载活跃花名册，因此不宣称整个接口内存与群规模无关。

## 迁移与回滚

新增 `0077`（依赖 `0076`）只增 `assistant_group_deliveries` 和 source/due 两个索引，无旧行改写。**本任务未在 116 执行迁移、部署或合并；上线前需人工决定迁移与发布顺序**：先迁移再替换 API/worker。回滚先换回旧应用，新表可保留；确认不需要批次历史后才 downgrade，downgrade 会删除该表。

## 验证边界

已覆盖解析、去重、上限整批拒绝、活跃成员过滤、零成员、明确拒绝/未知、并发重放、派发后回滚恢复、批次归组、运行状态、汇总恢复/不可用、成员权限、产物隔离/分页/时间、迁移升降级。模型边界为测试桩，尚无 116 上真实 16 run 的耗时、token/费用、模型汇总质量和前端卡片联调数据。
