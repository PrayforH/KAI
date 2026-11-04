# 助手页聊天铬去冗余（feat/remote-computer-20261103 基线，2026-11-04）

基线 `928a95a7`。本轮只改代码+本地测，**未部署 79**。与 managed 线 `41536576` 的意图一致，
但按本分支 `345a8560`（pending 消息进滚动历史）的实际结构手改，不硬搬 patch。

## 改了什么

1. **上方「查看更早的消息」按钮**：本分支 `agent-thread.tsx` 已无该按钮
   （`useAutoLoadEarlierMessages(…, { readerDriven: assistantSurface })` 仅作副作用，静默加载）。
   本轮补齐两处收尾：删掉残留的死 CSS（`.assistant-history-earlier` 两条规则）；
   `agent-thread-config.spec` 加源级守卫（源里不得再出现该按钮类名、钩子必须保留）。
2. **下方遮挡**（`prompt-queue.tsx` chat 分支 + `agent-thread.tsx` steering effect）：
   - 345a8560 的历史落位保留：能对账进滚动历史的消息（`historyRunId`）继续经
     `PendingMessagePlacement` 渲染在原轮次旁，永不在 composer 上方。
   - **无法落位的卡住消息**（unknown/failed、run 事件未加载或从未投递）此前会一直钉在
     composer 上方并挂着「接收状态待核对 / 重新发送」。经用户确认按聊天语义处理：
     **不留任何常驻控件，直接当新一轮发出去**——没有折叠行、没有重发按钮，气泡就是普通
     用户消息，随后自动作为下一轮投递（`releaseUncertainSends`：空闲时清掉 `receipt` 与
     `steerRunId`，`nextQueuedPrompt` 随即把它取出派发）。恢复 localStorage 时也不再打
     `receipt: "unknown"` 标记。防重复仍靠 `reconcilePromptHistory`：历史里已出现
     accepted/unknown 回执的行照旧被清掉或落位到原轮次，只有真无回执的才补发。
   - **空闲时释放 `steeringIds`**（steering effect 的 `!busy` 分支）：没有活动 Run 可核对时，
     刷新恢复的残留 in-flight 标记原本会永久阻塞队列派发与重发；释放后派发与重发恢复可用。
     已投递与否仍由 `reconcilePromptHistory` 对账决定，重复投递风险不因释放而变大。
3. **快输出/思考流**：本分支已是目标状态——助手面默认 compact
   （`compact = (compactComposer || assistantSurface) && !detailed`），思考行是默认收起的
   `<details>`（展开才挂载正文），设置里的「详细执行过程」可显式打开，未被破坏。无需改动。
   模型侧 `reasoning_effort/thinking` 旋钮在运行时不存在（`claude_sdk.py` 只传 `route.model`），
   加旋钮属 api/worker 改动，本 web 片不做。

## 分页为何仍慢（根因 + 已做的安全加速）

- **已加速**：更早页从每页 10 个 Run 提到 **30**（`THREAD_HISTORY_EARLIER_PAGE_RUNS`），
  翻页往返次数降到 1/3；最新页保持 10，打开线程的首屏不受影响。服务端接受大 limit
  （trace 控制台本就用 100）。有 spec 断言 earlier 页 `limit=30`。
- **剩余瓶颈 1（主因，未改）**：每个更早页都把**整个累积消息表**重新转换并整体 import 进
  assistant-ui runtime（`fromAgUiMessages(全部)` → `ExportedMessageRepository.fromArray` →
  `importRepository`）。成本随线程长度线性增长，长会话翻页总代价 O(n²)。根治需要
  runtime 级「只前插新页」路径，本轮不做。
- **剩余瓶颈 2（有意行为，未改）**：readerDriven 模式只在读者显式手势后 800ms 窗口内加载
  （上滚轮/↑/PageUp/Home/上滑），程序化滚动刻意不触发（有 spec 保护）——所以一次手势
  最多拉一两页，持续上翻需要持续手势。
- 次要：每页合并 `mergeHistoryMessages` 为 O(n)，相对瓶颈 1 可忽略；服务端 API 本身
  （limit=10 实测 p50 ≈84ms，见 managed 线基线）不是主要耗时。

## 如何验收（79 部署后）

- 打开旧长会话：顶部无按钮；上滚/↑ 静默加载更早消息。
- 运行结束后刷新（或上一轮留下卡住补充）：composer 上方只有一行折叠摘要，展开可重发。
- 运行中发送补充：气泡照常（本条不变）。

## 测试

- `npx tsc --noEmit` 干净。
- vitest 触及面：prompt-queue 7 + composer-interactions 2 项新增（补发语义）（新增空闲折叠/展开重发/已落位不进折叠/新 Run 中也不弹回 4 项，
  原「不确定传输」断言改为 busy 态口径）、task-history-pagination 11（+earlier 页 limit=30）、
  agent-thread-config +源守卫、steering-poller/unknown-replay 不回归。
- 全套：1638 过 + 12 失败；**12 项在干净 HEAD 逐名相同**（account-settings、
  agent-thread-config 2、assistant-activity-terminal、assistant-dup-channels、
  assistant-message-controls、assistant-task-routing 3、bot-rail-paused-badge 3），
  本片零新增失败。

## 发布 79（2026-11-04，web 单滚，rc 线）

用户要求直接单滚 rc web。镜像 `kai/superagent-web:20261104-7ba2e5d4`（干净两段构建自本分支
`7ba2e5d4`）；发布目录 `/data/kai-superagent-20261104-7ba2e5d4`。

闸门：CAS 比对 live compose == 7ffd833c 发布快照（无并发发布）；切换前后活跃 Run/职责执行均 0
（revision 0065）；schema **0065 无迁移**（web-only）；脚本内 assert api/worker/feishu 镜像逐项
不变（`20261103-4e559c28` / `20261102-4aaedddf`，均未重启）。

判别式指纹：`assistant-history-earlier` 类名在新镜像全部 chunk 中 **0 命中**（助手按钮已消失；
残留的「查看更早的消息」文字只在 studio 测试面板 chunk，非助手面）；「未发送的补充消息」新镜像
1 chunk、旧镜像 0。`http3501=200`、`http3543=200`、`healthz8080=200`。

**已知回退（用户确认接受）**：rc web 不含 managed 线 7ffd833c 的「管理职责」UI（rail 入口、
设置页第二入口、状态/重试、已结束折叠、maxNewTasks），随本滚暂时从 79 下线，待两线合并后回归。
**实页未点**：本次仅只读核对镜像/chunk/HTTP；交互效果待用户刷新页面自然核对。
