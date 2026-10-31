# 助手页三处缺陷修复（2026-10-31）

基线 `f1056a4f`，分支 `auto/kai-assistant-managed-20261030`。

## 1. 转交回执串进旧任务

- 根因：`/assistant` 入口把整页包在 `PersonalAssistantEntry` 里，打开普通任务后
  `usePersonalAssistantEntry()` 仍为 `true`；`HandoverInlineRows` 只判断这个值，
  于是子助理的 `pending_input` 回执被渲染进任务消息流。
- 修复：回执改为同时要求「助理面 + 非 bot 会话」（`shouldShowHandoverRows`），
  任务面或子助理自己的线程不再挂载。

## 2. 操作栏漂到右下、composer 偏移

- 根因：`assistant-answer-row` / `assistant-message-controls` 的 flex 布局只写在
  `[data-surface="assistant"]` 下；任务面共用同一段回答结构，控件回落到块级布局，
  同时回答气泡被 `codex-theme` 撑到 100% 宽。
- 修复：同一行 flex、底部对齐、控件 `position: static` 的规则改为所有
  `.chat-stage` 生效；任务面回答按内容收缩（`fit-content`，上限
  `min(640px, 92%)`）。composer footer 与外壳补上 `box-sizing`、居中约束，
  避免窄窗格里贴边/留白失衡。

## 3. 相邻重复的助理回答

- 修复在渲染层，不改提示词：
  - 单条回答内：只折叠「从行首开始、与回答开头逐字相同」的重复尾部
    （≥40 字符，避免误伤正常复述）。
  - 相邻 assistant 消息：文本完全一致时只保留第一条；运行中的消息不折叠，
    避免流式期间闪烁。
- 新增 vitest：`tests/assistant-duplicate.spec.tsx`。

## 自测

```bash
cd web/harness-console
npx tsc --noEmit
npx vitest run tests/handover-row.spec.tsx tests/assistant-duplicate.spec.tsx \
  tests/assistant-message-controls.spec.ts tests/assistant-answer-handoff.spec.tsx \
  tests/agent-thread-config.spec.tsx
npx vitest run   # 224/225 通过
```

全量套件唯一失败为既有的 `tests/account-settings.spec.ts`（设置导航项计数 11 vs 10），
与本次改动无关；其余 1413 项通过、1 项跳过。
