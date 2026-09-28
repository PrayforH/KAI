# 选择下拉收窄、移除图标与「内部智能体」展示（2026-09-28，174:3501）

## 反馈与结论

**1. 移除图标（×）没有垂直居中** —— 该标记是文本字符 U+00D7，其字身（ink）落在数学轴附近，位于 em 盒中线之上；几何测量显示字形盒与按钮同心（偏移 0），但视觉上偏上。改为与同排文件夹/箭头同一 16px 网格的描边 SVG（13px，stroke 1.6，圆头），`markCenterOffset: 0` 且视觉居中。

**2. 两个下拉太宽** —— 都是固定面板宽度，与内容无关：
- 知识库选择（composer `@`）：固定 `min(300px, 100vw-48px)`，实测内容只有「名称 + 类型」一行也占 300px。
- 智能体选择：固定 `min(360px, 100vw-52px)`，实测只有一个「通用助手」的账号也占 360px（截图证据：360×280 的空白面板）。
- 改为按内容尺寸：`width: fit-content` + `min-width` + 原宽度作为上限（知识库 `min-width 220px / max 300px`；智能体 `200px / 360px`）。实测：知识库 220px、智能体 200px。小屏媒体查询里的 `width: calc(100vw - 24px)` 与版本选择器自己的 240px 规则（特异性更高）不受影响。

**3. `长期记忆` 的下拉** —— 该页「当前智能体」是原生 `<select>`，规则 `width: 100%` 让它撑满整张卡片（实测卡片约 300–380px），即使只列一个短名字。改为 `width: fit-content; min-width: min(220px, 100%); max-width: 100%`，实测 220px。

**4. echo-agent / helper-agent 为什么还展示** —— 不是缓存或脏数据：这两者是**该账号自己发布过的真实智能体**（`agent_versions` 中 owner 为 `user_1c16a899…`，2026-07-30 发布；线上 registry 对该账号返回 7 个 agent，其中就含 `echo-agent`、`helper-agent`）。控制台默认**不展示**它们：`src/lib/agent-visibility.ts` 把它们与 `internal`/`parentDraftId` 一并视为内部对象，只有账号打开「设置 → 显示内部子智能体」时才会显示；该开关默认关闭，且本机 QA 账号的 localStorage 里没有这个键（即默认关闭）。
- 触发条件是**该账号的浏览器 localStorage 里 `harness:preferences:<user_id>:internal-agents = "true"`**（只有设置页那个开关会写它），我无法从服务端读到该账号的本地值，只能由账号侧确认。
- 因此：这不是缺陷而是开关状态；要让它们消失，关闭该开关即可。若希望「不显示内部子智能体」但**仍**保留 echo/helper 可用，则需要产品决定（把这条硬编码名单与开关解耦，或在智能体目录里给单条「在列表中隐藏」）。这两者都动的是产品行为，未擅自实施。
- 另注：该开关文案写的是「显示内部子智能体」，实际还会放出平台自带的两个测试智能体，措辞与语义不一致，值得后续收敛。

## 验证

- Web Vitest 全量 **959 passed / 1 skipped**；Next 构建 BUILD_ID `jlRGB-hONHa6ggvxhRS99`。
- 部署 174:3501：`kai/axis-web:develop-9331defd`（image `sha256:fca7c1a9…`，基座 `develop-034bd5d5`）。本轮 3599 canary 等到 **healthy + 首页 200** 后才切换（上一轮的 canary 未等到 healthy，已在上一份记录里标注）。切换后 3501 容器 healthy、restarts 0；旧容器 `axis-web-develop-034bd5d5-rollback-9331defd` 保留。API/三 Worker（`develop-7bdc9d81`）、3301、173 均未动。
- 浏览器实测：智能体下拉 360→**200px**（内容一项）；知识库面板 300→**220px**；长期记忆页 select 撑满→**220px**；移除标记为 SVG、中心偏移 0。

## 回滚

停 `axis-web-develop-9331defd`，把 `axis-web-develop-034bd5d5-rollback-9331defd` 改名回 `axis-web-develop-034bd5d5` 并启动；无数据库与 API 变更。173:3302 仍是同源未构建状态。
