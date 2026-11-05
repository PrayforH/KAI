# 助理回答重复 + 旧对话成果物胶囊：根因与修复（2026-10-31）

分支 `auto/kai-assistant-managed-20261030`，起点 `e2e3aaf9`（r33：已折叠"相邻重复 turn / 单条内重复尾部"）。
r33 之后用户硬刷新仍看到同一段莫高窟回答出现两次：一次是机器人图标旁的裸文本，紧跟一个浅灰圆角气泡再出一次。

## A) 重复回答：真因是「活动行把正文当状态短语」

**不是**相邻 turn 重复，也**不是**原生文本槽跳过 `HiddenAssistantText`（`HiddenAssistantText` 正常返回 null）。
用真实页面按文本反查 DOM 所有者（79，硬刷新 `/assistant`）得到唯一的两条链路：

```
span.grok-phrase-word < span.grok-phrase < div.grok-activity-row
  < div.latest-activity.succeeded < div.aui-assistant-message-root.harness-assistant-message
div.aui-md < div.assistant-answer < div.assistant-answer-row < div.aui-assistant-message-root
```

即：**同一条 turn 内**，正文既被画进 `.assistant-answer` 气泡，又被画进我上一轮做的活动行（脸 + 短语）。

根因链条（file:line）：

- `src/harness/agui/activity.py:516-527`：后端把**每一条 `message.delta`** 也记成一条 activity item，
  `summary` 就是当轮正文（`redact_text(text, limit=len(text))`）。
- `web/harness-console/src/components/agent-thread.tsx`（r24 的活动行）：取「最新一条带 summary 且非 `run.*` 的 item」
  当短语 → 命中 `message.delta` → 整段正文成了「状态短语」。
- `web/harness-console/src/components/grok-activity.tsx` 的 `ActivityPhrase` 原样逐词渲染该 summary。

r33 的折叠只处理 `message.delta` 拼接出的**回答字符串**，管不到这条独立通道，所以硬刷新仍在。

**修复**

- `grok-activity.tsx` 新增并导出 `progressPhrase(activity)`：只认真正的进展条目——
  跳过 `run.*`、跳过 `message.*` 与 `reasoning.*`（正文/思考不是进展），并丢弃超过 60 字的 summary；
  找不到就返回空串。
- `agent-thread.tsx`：`TurnActivity` 的聊天形态与「pre-response 活动行」统一走 `progressPhrase`；
  **短语为空时整行不渲染**（`if (!phrase) return null;`），不再画一张只有脸的空行或正文行。
- `GrokActivityRow` 再做一层截断（`MAX_PHRASE_CHARS` + 省略号），任何超长 summary 都不可能铺满一行。

改后同一页实测：所有「莫高窟」文本节点的所有者只有
`p < .aui-md < .assistant-answer < .assistant-answer-row`（助手回答）与
`p.aui-text < .aui-user-message-content`（用户提问）；`phraseRows` 全是「正在等待本轮模型结果」，
`phraseHasProse=false`。

## B) 成果物胶囊：双通道排查结果

- 文件类产物（`AssistantResults` / `ArtifactSummaryRow` / `ArtifactCard`）本来就只渲染元数据：
  实测旧线程的胶囊文本是 `HTML1 项产出` / `VIDPY2 项产出`，不含正文；预览走侧栏 `RailFilePreview`（点击后按需加载）。
- 真正会「把正文再印一张卡」的第二条通道是 **intelligent-ui 的 text 决策**：
  `presentation-host.tsx` 里 `presentation.decision.display === "text"` 时直接 `<p>{fallback_text}</p>`，
  而该轮的正文已在 `.assistant-answer` 里 → 又是一次重复。
  **修复**：`IntelligentUiBlock` 增加 `suppressText`，`agent-thread.tsx` 以
  `suppressText={Boolean(copyText.trim())}` 传入——本轮有回答正文时，text 决策不再复述；
  没有正文时仍保留 fallback（不吞掉唯一信息）。
  数据型 presentation（表格等）不受影响：79 上 11 行表格仍正常渲染（936 字，`.intelligent-ui-blocks`）。

## 测试

```
cd web/harness-console
npx tsc --noEmit                       # clean
npx vitest run tests/assistant-dup-channels.spec.tsx   # 7 passed（新增）
npx vitest run tests/assistant-duplicate.spec.tsx      # 既有折叠回归通过
npx vitest run tests/                                  # 1404 passed / 1 既有失败（account-settings，与本轮无关）
```

新增 `tests/assistant-dup-channels.spec.tsx` 覆盖：

1. `message.delta` 携带整段正文时，`progressPhrase` 只返回真正的进展（`正在生成回复`）；
2. 只有正文的 turn 返回空串，且渲染层 `if (!phrase) return null`（不会出现正文行）；
3. 跳过 `reasoning.*` 与超长 summary；
4. 活动行有长度上限截断；
5. presentation text 决策在「已有回答正文」时被抑制；
6. 文件胶囊只渲染 name/size/预览、不内联 body（`formatBytes` + `RailFilePreview`、无 `dangerouslySetInnerHTML`）。

## 自测方法

1. 79 上硬刷新 `/assistant`，任意长回答 turn：机器人图标旁应只出现一句状态（如「正在等待本轮模型结果」），
   正文只在气泡里出现一次；
   ```js
   [...document.querySelectorAll('body *')].filter(n => (n.textContent||'').includes('莫高窟')
     && ![...n.children].some(c => (c.textContent||'').includes('莫高窟')))
     .map(n => n.closest('.grok-phrase') ? 'PHRASE!' : 'ok');
   ```
   期望不再出现 `PHRASE!`。
2. 打开带表格 presentation 的旧线程（`dcb2016a-…`）：表格仍在，且没有额外的整段 fallback 段落。
3. 打开带产物的旧线程（`ac1cb89d-…`）：胶囊只显示「N 项产出」，点开文件走侧栏预览。

## 部署

web `kai/assistant-web:20261030-managed-r34` 已滚到 79（healthy，web=200）；上表证据即来自该版本真实页面。
