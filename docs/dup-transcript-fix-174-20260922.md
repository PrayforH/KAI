# 3501 重复打印修复 — 2026-09-22

Deploy record for the duplicated-transcript fix on 3501.

## Symptom

V4 任务重跑成功后（`run_599ac8b…`，07:15），对话记录里同一段思考/进度文本渲染两遍：
「配色与内容确认一致。现在重建生成器…」及后续思考块、工具行整段重复。用户报告
「以前的这种重复打印问题复现了」。

## Root cause

后端事件流与 durable 历史都干净（sequence 唯一、每轮一条 assistant 消息、id 稳定）。
重复出在前端 store 的合并语义：

- 活流把一个 run 推成 1,344 个碎片 item（1214 个 reasoning.delta、55 个 message.delta
  逐 token 碎片）。
- durable 历史投影把同一 run 压成 96 个 item（按 item_id / message_id 合并、保留首碎片
  event_id，id 集合是活流 id 的子集）。
- 历史重载时 `activityStore.publish` 走 `reduceRunViewModel` 的按 id 合并：96 个 durable
  item 覆盖了同名 id，但**被投影折掉的 1,200+ 个碎片 item 仍然留在 view model 里**。
- `commentaryNodes` 按 item_id 分组拼接文本 → 同一段思考文本拼了两遍（实测 974 字符
  变成 1,934 字符，头尾与原文逐字一致）。

之前 09-20 的 news-stream 独立构建在客户端做了流压缩，间接避开了这个合并场景；
09-21 的 develop 重建没有带上那份工作，问题随之回归。

## Fix（e95cacbd）

durable 历史发布对**同一 run** 改为替换而非合并：`activityStore.publish(activity,
threadId, { replaceRun: true })` 时若 `viewSnapshot.runId === activity.run_id`，view
model 从 durable 活动整体重建，碎片 item 全部清掉。活流路径（snapshot/patch）仍是合并，
流式增量不受影响。`publishHistoryActivity`（task-history.ts）是唯一传 `replaceRun` 的
调用点。

回归测试：`tests/activity-store.spec.ts` 新增「merge by default / replace when asked」
用例；另有基于 174 真实数据的本地复现（live 974 字符 → 合并后 1,934 → 替换后 974），
数据文件在 /tmp，不入库。

验证：harness-console 全量 vitest 122 文件 779 passed / 1 skipped。

## Deploy

- 镜像 `agent-studio-web:develop-20260922-e95cacbd`（digest `885a364c…`），由
  `scripts/build_harbor_174.sh` 只构建 web（api/worker 无需动，沙箱修复已随
  `develop-20260921-3f58c360` 在线上）。
- 部署前从镜像抽出构建产物 grep `replaceRun` 标记确认包含修复。
- 3501 用 `update_3501.sh develop-20260922-e95cacbd` 切换：`axis-web-20260922-e95cacbd`
  healthy，首页 200，服务端与客户端 chunk 均带标记，旧容器
  `axis-web-20260921-3f58c360-rollback-develop-20260922-e95cacbd` 保留可回滚。

注意：`fix/develop-review-20260921` 是**两个会话共用**的 worktree，另一个会话同时在
上面提交并部署（composer 改动 `3f58c360`）。本次 web 镜像以 `e95cacbd`（其父链含
`3f58c360`）构建，相对线上只多本次 3 个文件的修复。

## 顺带排查：inputs/ 又被清空了

07:14 的重跑（`run_80ed6d2b…`）里 agent 报告「沙箱这一轮是全新的空工作区，inputs/ 现在
是空的」。原因不是新的清空逻辑：早上 4 条失败 run 都在 collect 处崩掉，**没来得及归档
工作区快照**，会话快照停留在 09-20 03:03 的旧内容；07:14 的 run 恢复快照自然没有 V4 输入。
沙箱修复上线后（07:10）快照链已恢复推进（07:14 → 07:16 → 07:19 逐次归档/恢复），07:15
重跑带输入即正常产出。残余行为：**失败 run 不会推进会话快照**，若未来 run 再失败，
下一次重跑仍会恢复到上一次成功的快照——这是现有设计，需要改产品行为再动。

## Rollback

`bash /data/agent-studio/update_3501.sh develop-20260921-3f58c360` 即可退回上一版。
