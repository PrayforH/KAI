# 转交后补齐配置 → 启用 → 首跑入队（/assistant 面）2026-10-31

分支 `auto/kai-assistant-managed-20261030`，起点 `0ee6fffd`。

## 现象与定位

在 `/assistant` 把职责转交给子助理（如「休息提醒」）后，子助理会话「一点输出都没有」。

后端行为是**如实的**，不是静默失败：
`SPECIALIST_REQUIRED_CONFIG = ("schedule_minutes", "notify_channel")`；
`Service.delegate_to_specialist` 在 `missing` 非空时把 handover 置 `pending_input`
并**故意不入队**（`src/harness/assistant/service.py:1076-1092`），
回执文案也写明「缺项不启动」。`2f92a1ec` 之后，配置齐全 → `applied` + 入队、
启用时给尚无执行的职责补一次首跑，两条后端路径都有集成测试
（`test_applied_delegate_enqueues_exactly_one_first_execution`、
`test_completed_specialist_config_enables_first_execution`）。

**真正的缺口是产品路径**：补齐配置的 UI 只在 `agent-studio/assistant-manager.tsx`（另一个页面），
助理面 `/assistant` 的子助理会话里没有任何入口 → 用户看到「待补充」却无处可点，
于是表现为"转交完什么都不跑"。

## 改动

新增 `web/harness-console/src/components/specialist-setup-card.tsx`，
在 `/assistant?bot=<id>` 的子助理会话里渲染。它挂在 `.chat-surface`（flex 列）的
滚动区之上，是**对话区顶部的独立卡片**：不随消息滚走，也不占用右侧详情面板；
状态变成 active 后整块消失，把空间还给对话。

- 标题「还差两步就能开始跑」+ 说明「交办已收下，但缺少检查频率和通知渠道；
  补上并启用后，第一次执行会立刻入队」
- 检查频率三档：`每小时 / 每 6 小时 / 每天`（60/360/1440 分钟），
  预选值读自已存的 `config.schedule_minutes`
- 通知渠道：站内消息（inbox，当前唯一渠道，置为已选）
- 主按钮「保存并启用」→ **严格两步**：
  1. `POST specialists/{id}/config`（携带 `schedule_minutes` + `notify_channel`，
     `expectedRevision` = 当前 revision）；服务端写入后落到 READY，并把频率同步到职责
     （`_sync_duty_schedule`，否则 due worker 看不到 `next_check_at`）
  2. `POST specialists/{id}/status {status:"active", expectedRevision: 第 1 步返回的 revision}`
     → 服务端 `_start_duty_if_idle` 为还没有执行的职责补一次入队
- 成功后卡片变成「已启用，首次执行排队中」，并通过 `onReady` 让页面
  重取 bot 详情（`botRevision + 1`），会话随之接上真实线程与首条执行。
  这条回执只在本次点击后显示：页面重新加载时若 bot 已是 active 就整块不渲染，
  不会把一次性的「排队中」变成常驻横幅（那时首跑可能早已跑完）
- 状态在 `active` 不渲染（已启用无需再补）；没有绑定职责时改为提示
  「先让主助理给它派一件事」；失败时把服务端错误（如「还没有绑定职责」）原样展示，按钮保持可点

`src/app/page.tsx`：在 bot 视图内挂载该卡片（`bot && <SpecialistSetupCard …/>`）。
样式 `web-codex.css` 中 `.specialist-setup-card` 一组，仅作用于
`.chat-stage[data-surface="assistant"]`。

> 未改委派状态机语义、未改飞书/微信入站、未做右侧详情面板。

## 测试

```
cd web/harness-console
npx tsc --noEmit                                  # clean
npx vitest run tests/specialist-setup-card.spec.tsx       # 4 passed（结构：三档频率/站内消息/主按钮）
npx vitest run tests/specialist-setup-card.flow.spec.tsx  # 3 passed（jsdom 真实点击）
npx vitest run tests/                                     # 1415 passed / 1 既有失败（account-settings，无关）
```

流程测试用 jsdom + `createRoot` 真挂载组件、mock `assistantRequest`，断言：

1. 点「每小时」→「保存并启用」后调用序列为 **config → status**，且
   `config.expectedRevision = 3`、`config.config.schedule_minutes = 60`、
   `notify_channel = "inbox"`、`status.expectedRevision = 4`（用第 1 步返回的 revision）、
   `status.status = "active"`；
2. 成功后出现「已启用，首次执行排队中」且 `onReady` 被调用；
3. 加载时 bot 已是 active（配置齐全、早前已启用）→ 整块不渲染、没有残留的「排队中」横幅；
4. 启用失败时展示服务端错误文案、按钮恢复可点。

后端既有集成测试覆盖「配置完整 → applied + 恰好一条 queued execution」与
「启用后补首跑」，本轮未改后端，未重复添加。

## 79 冒烟（只读）

web `kai/assistant-web:20261030-managed-r36` 已滚到 79（healthy，`/assistant` = 200）。
r36 构建 ctx `web-src-r36`（由 r35 源码复制后覆盖本轮 3 个文件，逐文件 sha256 对账一致），
`web-r36.Dockerfile` 以 `kai/assistant-web:20261030-managed-r4` 为运行基座。
卡片的 page chunk 哈希由 `2be9lq7pqnt3n.js` 变为 `0z38mvebel7wp.js`，说明镜像里确实是新代码。

用纯 HTTP `http://172.20.105.79:3501/assistant?bot=specialist_466780791f3c4f218b66019818346459`
（休息提醒）实测：

```json
{ "card": true, "state": null, "title": "还差两步就能开始跑",
  "buttons": ["每小时","每 6 小时","每天","站内消息","保存并启用"],
  "每天.selected": "true", "站内消息.disabled": true,
  "staleBanner": false }
```

- 卡片渲染在对话区顶部：标题「还差两步就能开始跑」、默认选中「每天」、站内消息已选且不可改
- 页面上没有「已启用，首次执行排队中」（needs_input 的 bot 不该出现旧回执）
- 因涉及真实数据（会把该 bot 置为 active 并按频率持续运行），冒烟**只验证渲染**，
  没有替用户点击启用；点一次即可完成「补齐→启用→首跑入队」。
- 截图：`/tmp/kai79-accept/151-setup-card-r36.png`

## 如何在 /assistant 点验

1. 打开主助理 `/assistant`，侧栏「待启用」里的子助理（如「休息提醒」），或主对话里的转交状态行
   「待补充 ·「休息提醒」」——点击进入该 bot 会话；
2. 顶部卡片选频率（默认每天）→ 点「保存并启用」；
3. 卡片变为「已启用，首次执行排队中」，侧栏该 bot 从「待启用」移到「在岗」；
4. 稍等（首个执行入队 → worker 接单），会话内出现运行活动/首条结果；
   「…」编辑弹窗的例行程序区显示周期与「已激活」。

## 追加（2026-11-01）：主会话亦可内联

主 `/assistant` 出现 `pending_input` 转交行时会直接挂同一张 `SpecialistSetupCard`，
选频率 →「保存并启用」即可完成 config→status 与首跑入队，**无需**跳到 `/assistant?bot=…`；
成功后行态由「待补充」变为「已安排」，卡片收起。详见
`docs/results/2026-10-30-kai-assistant-managed-79.md` 的 kai-main-session-pending-input-card 小节。
