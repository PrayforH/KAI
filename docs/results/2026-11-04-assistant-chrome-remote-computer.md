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
     composer 上方；现在运行中（busy）保持原样可见，**空闲时折叠为一行灰色
     「未发送的补充消息（N）· 展开可重发」**，展开才有气泡与「重新发送」（onSend 走既有
     追加为新一轮的路径），输入区不再被遮挡。
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
- vitest 触及面 25 过：prompt-queue 8（新增空闲折叠/展开重发/已落位不进折叠 3 项，
  原「不确定传输」断言改为 busy 态口径）、task-history-pagination 11（+earlier 页 limit=30）、
  agent-thread-config +源守卫、steering-poller/unknown-replay 不回归。
- 全套：1638 过 + 12 失败；**12 项在干净 HEAD 逐名相同**（account-settings、
  agent-thread-config 2、assistant-activity-terminal、assistant-dup-channels、
  assistant-message-controls、assistant-task-routing 3、bot-rail-paused-badge 3），
  本片零新增失败。
