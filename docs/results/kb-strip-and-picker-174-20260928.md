# 知识库条带降高与选择弹层防裁剪（2026-09-28，174:3501）

## 用户反馈

1. 输入框上方的知识库绑定条带（「人工智能政策 ▾ ×」）高度太高，要求降低。
2. 点开条带上的图标后，弹出的选择窗口只剩一小块（右侧部分的一点窗口）。

## 排查

- 条带为 `composer-context-shelf task-knowledge-selection`：芯片触发器 `min-height: 36px` + codex 主题 shelf `padding: 4px 12px 16px`（负 margin 与输入框合并），实测整体 **58px**。
- 「点开只剩一小块」在 174 当前构建上先按标准路径复现失败（正常窗口下弹层完整锚在芯片上方，截图证明）。压矮视口到 480px 后复现：弹层自触发器向上展开（`bottom: calc(100% + 8px)`，max-height 至 360px/55dvh），而线程滚动容器 `.aui-thread-viewport` 是 `overflow: auto` 裁剪盒——弹层一旦高于触发器上方的实际空间，**顶部（标题+搜索框）被裁掉，只剩一小块窗口**。裁剪与否取决于窗口高度与弹层内容的自然高度，与用户环境的窗口几何一致。
- 该组件与 CSS 在 develop 与 173 演化分支（auto/agent-evolution）完全一致，两侧行为相同。

## 修复（`856abf15`，仅 Web）

- `web-codex.css`：芯片触发器 36px→26px、字号 13→12px、移除按钮 22→20px；KB 条带专用 shelf padding（基础 4/8，codex 2/10），不动共享的 prompt-queue 条带。条带整体 58px→**40px**，输入框上方可见高度约 46px→28px。
- `task-knowledge-context.tsx`：弹层打开时按触发器上方实际空间设置 `max-height`（上限仍 360px，下限 160px），滚动容器从此裁不到它。选择规则/锚定逻辑不动。
- 回归测试：`tests/knowledge-answer.spec.tsx` 新增「触发器贴近视口顶部时 maxHeight 被钳制」断言。

## 验证

- Web Vitest 全量 958 passed/1 skipped；Next 构建 BUILD_ID `bl4Rs2lUKI8UzzMgugwt6`。
- 部署 174:3501：`kai/axis-web:develop-856abf15`（image `sha256:b61f223b…`，revision label `856abf15…`），基座 `develop-953d8cc2`；3599 canary 四端点 200/401 后切换，新容器 healthy/restarts 0；旧容器 `axis-web-develop-953d8cc2-rollback-856abf15` 保留。API/三 Worker（`develop-7bdc9d81`）、3301、173 均未动。
- 线上 chunk 自证：3501 提供的 `29nqp_blvllk2.css` 含 `min-height:26px` 与新 shelf 规则。
- 浏览器复测（注入真实类名的芯片+弹层）：720 视口条带高 40px、弹层完整且 `fullyInsideViewport: true`；480 矮视口弹层不再越界（此前复现的裁剪消失）。长列表场景的钳制由单测覆盖。
- 3301、173:3302 均 200 未受影响。

## 说明与回滚

- 173:3302 的代码与本修复同源但构建未包含；要同步需将该修复随 develop 合入演化栈再发 3302。
- 若用户在正常窗口高度仍见「右侧部分窗口」，需要弹层打开状态的截图进一步定位（本轮修复已覆盖已复现的唯一裁剪机制）。
- 回滚：停 `axis-web-develop-856abf15`，将 `axis-web-develop-953d8cc2-rollback-856abf15` 改名启动即可；无数据库、无 API 变更。
