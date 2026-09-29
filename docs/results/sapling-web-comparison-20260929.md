# 小树 2.0 Web 体验代码对照

参考本地 `/Users/xiaokai/work/python_workplace/sapling_buddy`，
origin `https://git.shdata.com/shihang/sapling_buddy.git`，提交 `4f144f5`（2026-09-29 14:15）。
只读分析，没有修改小树代码，也没有启动其业务服务。本次未进行两端同模型的浏览器性能实测，
以下区分代码事实与体验推断。

| 环节 | 小树代码事实 | AXIS 当前代码事实 / 可借鉴方向 |
| --- | --- | --- |
| 首段显示 | token 直接追加 bubble.raw；markDirty 在 requestAnimationFrame 中批量渲染 Markdown | live-response-store 有候选等待：180ms 或达到 160 字符后可见；可优先消除这段人为等待 |
| 中途工具 | 已输出正文在 tool_start 转为默认展开的 process 步骤，done 后再收起自动展开的步骤 | 正文在 live response 与 activity 两个显示入口间交接；应明确无空白帧、无重复、展开状态稳定 |
| 过程区域 | tree-children 最大高 400px、内部滚动；每步标题固定、详情折叠 | 当前过程随内容增长，结束时整个 process 自动关闭；限制高度及更稳定的结束布局可能改善正文位移 |
| 滚动 | token 跟随直接吸底；发送 / 点击回到底部使用 300ms cubic ease-out；wheel/touch/键盘立即打断 | 同样支持用户上滚暂停，但 resume 走 instant；可只给用户主动跳转增加可中断缓动 |
| 动效 | 新步骤 180ms 淡入上移；展开 grid 0fr→1fr 180ms；hover 150ms；减少动画偏好降级 | 已有 160ms 淡入及 180ms 折叠，差距主要在触发时机、位置和连续性，不需要重复增加动画队列 |
| 正文结束 | 后端 answer_end 在标题 / 来源持久化之前发送，前端立即熄灭光标；done 管最终收尾 | 已有 textComplete 与 Run 状态分离；保留此行为，不让正文光标等待收尾 |

小树并没有在这里增加一个逐字打字机队列；按帧刷新与清晰的状态交接更能解释观感。
两端都是按帧更新，不能仅凭 Vue 与 React 的框架差异断言性能高低。

## 参考位置

- `frontend/src/composables/useChat.js:207`：markDirty / rAF 批量 Markdown。
- `frontend/src/composables/useChat.js:273`：tool_start 时展开保留中间正文。
- `frontend/src/composables/useChat.js:324`：answer_end / done 分离。
- `frontend/src/components/ChatView.vue:209`：滚动跟随和 300ms 用户跳转。
- `frontend/src/chat-trace.css`：400px 过程区域、180ms 折叠和弱动效。
- `app/services/agent_service.py:610`：正文完成先于持久化收尾。

AXIS 对应入口：`live-response-store.ts`、`activity-summary.tsx`、
`conversation-scroll.ts`、`process-disclosure.tsx`、`conversation-experience.css`。

建议先做显示时序、滚动和过程区布局的局部改进，保留已验证的延迟优化与稳定 Item ID。
不建议直接搬用小树整份 timeline：它按末尾相邻类型合并思考，并对 terminal 工具复用节点，
不等于已解决异步回调交错、并行工具及重放身份问题；Markdown 每帧重新解析全文也需防止长文成本。
这些建议尚未实施，本轮产品改动仅为已经验收的思考碎行修复。
