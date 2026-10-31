# 本机 Grok Bot 客户端源码调查（用于对话形态对齐）

调查对象：`/Applications/Grok Bot.app`，`CFBundleIdentifier = com.anysphere.sand`，版本 **0.78.1**，
Electron 打包（`Contents/Resources/app.asar`，52MB，899 个条目）。只读解包到 `/tmp/grokbot-asar/extract`，
未运行其中的任何代码。渲染层主 CSS `dist/renderer/assets/index-*.css`（766KB），主 bundle
`index.eager-app-*.js`（4.5MB），中文文案在 `dist/electron-main/locales/zh-CN/messages.cjs`。

## 1. 设计令牌（可直接对齐）

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--conversation-bubble-max-width` | `min(640px, calc(100% - 0px))` | 气泡最大宽度 |
| `--conversation-font-size` / `line-height` | `14px` / `22px` | 正文 |
| `--sand-fill-bubble-agent` | `#1c1c1c0f`（6% 前景） | 助手气泡填充 |
| `--sand-fill-bubble-user` | `#1c1c1c` | 用户气泡（中性） |
| `--sand-fill-accent-bubble-user` | `#126fd4` | 用户气泡强调色（主色） |
| 每 bot 配色 | blue `#126fd4` / purple `#7442d1` / magenta `#c82779` / green `#0d9655` / orange `#d2550a` / red `#c82532` / cyan `#0d9381` / brown `#86592e` / yellow `#c2730b` | 头像与用户气泡同色 |
| 圆角刻度 | 4 / 6 / 8 / 12 / 14 / 16 / 18 / 9999 | `--cursor-radius-*` |

结论：**样式可以完全对齐**，因为气泡只有"填充色 + 圆角 + 最大宽 + 14/22 字号"这几项。

## 2. 每个 bot 一台"电脑"与授权模型

中文字符串里反复出现：`Grok Bot 的电脑`、`在 Grok Bot 的电脑上运行命令`、
`正在等待你在 Grok Bot 的电脑上登录`、`交还电脑后 Bot 未能恢复运行`。
权限是**按能力逐项询问**的（与我们的"用户授权 ∩ 职责范围 ∩ 模板 ∩ 连接权限"交集同构）：

- 允许 X 运行命令吗 / 在你的电脑上运行命令吗
- 允许 X 使用已连接的应用吗 / 访问你的私人文件吗
- 允许 X 运行后台任务吗 / 保存例行任务吗 / 分享 Bot 吗
- 允许 X 使用云端智能体吗 / 通过 <server> 发送吗 / 从 <server> 删除内容吗

还有一条值得借鉴的安全默认：`这个 Bot 的例行任务在无人查看结果时仍持续运行，因此已暂停以避免浪费用量`
——无人查看即暂停例行任务，避免空烧。**已实现**（P1-1 后端闸门 + 本切片 UI/触达口径）：
`Specialist.lastViewedAt` / `routinePausedReason` 在顶层；阈值
`HARNESS_ASSISTANT_UNVIEWED_PAUSE_HOURS`（默认 24h）；进入 Bot 会话（`/assistant?bot=…` 加载线程）
才 POST `specialists/{id}/viewed` 清暂停，GET 详情与编辑弹窗只读、不算查看。

## 3. 主对话如何让别的 bot 干活

截图里的居中细状态（`已发送消息给 X`、`3 条消息往来：X`）就是跨 bot 派活的可视化：
主 bot 把消息/任务转给某个 bot（该 bot 有自己的电脑与会话），转交本身在**主对话里以状态行出现**，
点开进入该 bot 的会话或面板。计划（`例行任务`）属于 bot 而非主对话。

我们的对应实现：`assistant_delegate_to_specialist` + `assistant_handovers`（sent/received/applied/
pending_input/failed），本轮补了主对话里的转交状态行（`.handover-strip`，居中细状态）。

## 4. 本仓库已采纳 / 未采纳

采纳：
- 气泡令牌（宽度 640、14/22、6% 填充、18px 圆角）；
- 每 bot 一个强调色（头像与用户气泡同色，主助理固定蓝 `#126fd4`）；
- 侧栏 bot 头像与新建/编辑保存的 appearance（色 + 形 blob|drop|block|star|cloud|pebble）对齐，
  主助理仍是固定「K」入口；顶栏（会话标题左侧）与活动行的脸也已接入同一外形，与 rail 一致；
- 顶栏电脑入口旁显示共享桌面的只读占用/排队（接 P1-4 `desktop/occupancy`，空闲时隐藏）；
  chip 可读排队位次（`排队中 #n`）与中文等待原因，长文本靠 ellipsis + aria/title 保留全文；
- 助理面「后台任务进展」浮标展开后，每条终态进展显示检查点计量（tokens/cost/耗时，未计量显式标注）；
  失败进展卡可直接打开原线程的失败回放（thread + run + checkpoint + trace=1，与托管面板同判）；
  skipUnchanged 早退（未唤起模型）单独显示「未唤起模型 · 无变化」，不冒充「tokens 未计量」；
- 左侧 rail 对「无人查看已暂停」直接标「已暂停 · 无人查看」（琥珀副标题），不必进编辑弹窗；
  打开该 Bot 会话（viewed 成功）后列表立即重拉，暂停标不需要等下一次轮询；
- bot 的会话即承接职责的原线程（无替身会话），结果直接落在对话流；
- 主对话转交状态行；
- "无人查看就暂停例行任务"：`schedule_due` 对绑定 Bot 的职责超过 24h 未查看则跳过入队、
  写顶层 `routinePausedReason`，查看后下一次到期恢复；编辑弹窗显示「已暂停 · 因无人查看已暂停，打开本会话后恢复」。

未采纳（记录原因）：
- 右侧"详情/资料库/电脑"面板：用户确认属客户端特性，web 暂不做；
- 团队/分享 Bot、频道消息、云端智能体等：不在第 8 章范围；
- "无人查看就暂停例行任务"：已落地（见上），不再是"下一步"。
