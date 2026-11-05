# 终态后清掉「正在等待本轮模型结果」（活动行）2026-10-31

分支 `auto/kai-assistant-managed-20261030`，起点 `dce054a7`（web r34）。

## 现象

用户问完后助理已给出终态回答（演示 `assistant_delegate_to_specialist`，回执 `pending_input`），
但对话流里仍留着一行「机器人脸 + 正在等待本轮模型结果」。

## 根因（79 真实页面取证）

读取页面上每一条活动行（`/assistant`，硬刷新）：

```json
{ "cls": "latest-activity succeeded", "source": "captured-turn",
  "text": "正在等待本轮模型结果", "face": "done" }
```

即：**run 已 `succeeded`（终态）、脸已 `done`，却仍在渲染一条瞬时等待短语**。

- 数据来源：`runtime.system` 的 `requesting` 状态被后端记成 activity item，
  summary 就是「正在等待本轮模型结果」（`src/harness/agui/activity.py` 的 runtime.system 分支），
  它随 durable activity 一起回放，落在历史 turn 上。
- `dce054a7` 的 `progressPhrase()` 只排除了 `message.*` / `reasoning.*` / 超长 summary，
  **没有**区分「瞬时等待」与「真实进展」，也**没有**看 run 是否终态 →
  终态 turn 仍会挑到这条 summary 并画出整行。
- 与客户端语义不符：交办/回合结束后中间状态应消失，只留结果。

## 修复

`web/harness-console/src/components/grok-activity.tsx`

- 新增 `TRANSIENT_PROGRESS` 与 `isTransientProgress()`：
  「正在等待本轮模型结果 / 正在生成回复 / 正在准备运行环境 / 正在整理本轮结果 /
  正在生成可恢复的上下文摘要 / 正在处理」等瞬时文案。
- 新增 `ACTIVE_PROGRESS_STATUSES`（queued/pending/provisioning/running/waiting/
  waiting_approval/cancelling）与 **`shouldShowChatActivityRow(status, activity)`**：
  有合格进展 **且** run 仍进行中才画行；`succeeded / completed / failed / cancelled`
  一律不画。委派 `pending_input` 的回执回合同样是终态 → 等待行一并消失。
- `progressPhrase(activity, { allowTransient })` 支持在需要时排除瞬时文案
  （便于将来在终态展示"真实进展"时复用），默认保持原行为。

`web/harness-console/src/components/agent-thread.tsx`

- 聊天形态活动行（`TurnActivity`）与「pre-response 活动行」都改为先过
  `shouldShowChatActivityRow(...)`，不满足整行不渲染。

进行中仍会短暂显示真实进展短语（`running/queued/waiting_approval` 通过）。

## 测试

```
cd web/harness-console
npx tsc --noEmit                                     # clean
npx vitest run tests/assistant-activity-terminal.spec.tsx   # 5 passed（新增）
npx vitest run tests/assistant-dup-channels.spec.tsx        # 7 passed（回归 dce054a7）
npx vitest run tests/assistant-duplicate.spec.tsx           # 既有折叠回归通过
npx vitest run tests/                                      # 1409 passed / 1 既有失败（account-settings，无关）
```

新增 `tests/assistant-activity-terminal.spec.tsx` 覆盖：

1. `succeeded / completed / failed / cancelled` + 等待短语 → 不渲染行（含用户报告的场景）；
2. `running / queued / waiting_approval` + 真实进展 → 仍渲染；
3. `isTransientProgress` 判定、`allowTransient:false` 时丢弃瞬时文案但保留真实进展；
4. 两个聊天形态活动行都经同一谓词门控（源码断言）；
5. `dce054a7` 的「message.delta 不当短语」「suppressText」仍有效。

## 如何自测

1. 部署后硬刷新 `/assistant`：终态回合上方不再有「正在等待本轮模型结果」。
   79 现网取证显示**所有**历史行状态都是 `succeeded`，因此部署后这些行全部消失；
   快速断言：
   ```js
   [...document.querySelectorAll('.latest-activity')].map(n => n.className)
   ```
   期望只剩 `running` 类（或空数组）。
2. 发起一轮新对话：进行中应短暂出现「正在等待本轮模型结果」等进展；回答结束时该行消失，只留结果气泡。
3. 演示跨 bot 转交（`assistant_delegate_to_specialist` 缺配置）→ 回执 `pending_input` 后，
   等待行不应残留；转交状态行（`已发送给「X」`）仍在。

## 备注

本次未部署 79（按要求由父级统一滚动）。行为证据：79 现网所有活动行 `cls` 均为
`latest-activity succeeded`，与新增谓词的隐藏条件完全一致。
