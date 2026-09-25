# 174 Web 终态答案交接修复验收

日期：2026-09-25。分支 `perf/thread-run-latency-174`。后端仍为 `perf-20260925-b12e5550`（API＋3 Worker），3301 Web 灰度 `perf-web-20260925-12fefe79`，3501 未动。发布前后均核对非终态 Run 为 0、Web 25 个环境变量继承、容器 healthy、首页 200。174 overlay 备份 `/data/agent-studio/docker-compose/compose.deepagents-174.yaml.bak-perf-web-12fefe79` 可恢复到诊断镜像；更早原版 Web 镜像为 `develop-20260922-d69cb0d2`。

## 根因证据

同一验证账号独立线程 `8435675b-9e4d-4e6e-a841-fe51ceb6fb73`，第六轮 `run_cfc7f322032a45eb943227ff30814171` 和第七轮 `run_f2e0702d6ad94e219363c8067d026872` 在原行为下复现：终态 `message.content` 与 `message.parts` 均含 `text,tool-call`，父级 `data-turn-answer="OK"`、`data-direct-stream=false`，但 `.assistant-answer` 不存在；第七轮 Text 子组件连诊断性 `return null` 标记都没有挂载。live 阶段抑制正确；终态不是旧 history 清空，也不是工具前文本规则触发，而是把最终正文寄托于 assistant-ui 的 Text part 子组件时，该子组件在现场没有挂载。刷新后历史恢复正文。

## 修复

提交 `12fefe79`：运行中的 Builder/原生消息继续使用原有 `HarnessAssistantText`；完成后的 durable 正文改由 `HarnessAssistantMessage` 父级从与“复制回答”一致的 `copyText` 渲染，live 所有权仍由 `LiveAssistantResponse` 接管，完成时禁止重复文本节点。`TextMessagePartProvider`＋`MarkdownText` 继续处理格式与引用；Reasoning/工具/Artifact/视频 part 仍留在 `AssistantMessage.Content`。`83243b94` 增加真实 assistant-ui runtime 的工具前说明→Read→最终文本→活动投影回归，断言仅最终正文出现一次。

本地：Web 全量 921 passed、1 skipped，`npx tsc --noEmit` 与 `npm run build` 通过。Builder 专项 37 项通过；真实 runtime 组件测试验证短 `OK` 和工具边界。

## 174 浏览器灰度

同一线程第八轮 `run_f037203d28454f0a802f42f65f3606f9`、第九轮 `run_67cedcc086ba4737b68928c1f9da32cd`：完成后不刷新，`data-turn-answer=OK`、`data-direct-stream=false`、恰好一个 `.assistant-answer` 且文本 `OK`；Run 显示完成。第十轮 `run_c903d4e975ba48f4b55fd7f6827f9bc6`：多行 Markdown 回答以 `<strong>甲</strong>` 和列表项 `乙` 正确渲染，不刷新亦保持正文。未观察到此前的完成后空白；三轮是功能灰度，不是浏览器 p95 统计。

## 追加终态验收与首字口径

提交 `83243b94` 后 Web 全量 **921 passed、1 skipped**；174 同线程第九轮 `run_67cedcc086ba4737b68928c1f9da32cd` 不刷新仍恰好一个 `OK` 正文；第十轮 `run_c903d4e975ba48f4b55fd7f6827f9bc6` 的 Markdown `<strong>甲</strong>` 与列表 `乙` 正常；随后两轮 `run_8432d9be07ae436ca17415a4c9cc75eb`、`run_2f53273077bd4cd790b94b6b654b21b9` 均终态 `OK`，无重复回答。浏览器计时探针两次分别误读上一轮文本和 assistant-ui 重建后的行，**均不纳入首字 p95**。本报告只验收终态正文稳定，不宣称浏览器首字 p95 已达标。

## 浏览器首字计时边界（追加）

同线程 `run_8d3222b2b7524a9a81924fca7ad6ddbb` 用新 Run ID 绑定 DOM 观察：`run.queued` 后服务端首 `message.delta` 约 3.08 s，`run.succeeded` 约 4.32 s，`history.snapshot` 约 5.42 s；浏览器 `.assistant-answer` 首次非空约 5.66 s，终态正文稳定 `OK`。该单次样本的终态交接有约 1.1 s 长尾，但不是 p95。另两次探针误把上一轮答案/被重建的 assistant 行当作新首字，判无效，不入统计。该长尾与首次终态 `/history` 回退路径有关：当没有 `history.snapshot` 时，`src/harness/agui/routes.py` 在折叠 RunEvent 后同步等待 `EventService.append` 写回 snapshot。`run_8d3222...` 的 snapshot 比 `run.succeeded` 晚约 1.10 s，DOM 正文更晚约 0.24 s；这是时间相关而非单独阶段的因果证明。不能简单改为请求内 fire-and-forget/FastAPI BackgroundTasks：进程退出会丢写入，并发 GET 可重复写 snapshot。若要移出关键路径，应先设计幂等的持久投影/可恢复任务，并补并发和重启测试；本轮未修改该路径。一次关闭 live/durable 二次平滑的本地实验通过测试，但只能解释几十毫秒且未证明 1.1 s 收益，已撤回，未部署。

## 30 个有效浏览器 Run 的分段 p95

严格按新出现的 Run ID 绑定 DOM 节点，串行采集 33 个尝试，其中 30 个满足 `run_id`、终态正文为 `OK`、单一 `.assistant-answer`；3 个页面/采样器长停（约 60 秒）单列，不静默纳入。原始 ID 与排除项见 [浏览器样本 JSON](perf-browser-174-valid-20260925.json)。有效稳定正文：首非空 DOM p50 **5,787 ms**、p95 **9,877 ms**；终态标记/正文稳定 p50 约 **5,001 ms**、p95 **9,877 ms**。该测量为顺序单页 IAB 交互，浏览器 p95 会受页面刷新/虚拟化/assistant-ui 调度影响，服务端事件作为分段权威。

对其中 28 个可完整读取服务端事件的 Run：`queued→message.delta` p50 **3,092 ms**、p95 **3,623 ms**；`queued→run.succeeded` p50 **4,270 ms**、p95 **4,838 ms**；`queued→history.snapshot` p50 **5,045 ms**、p95 **8,569 ms**。因此浏览器稳定正文比服务端首 delta 多出约 6.3 s（p95），且 snapshot 写回与终态交接位于同一长尾区间；这不是模型 API 时长。延迟 history reconciliation 的 174 单次灰度 `run_54d549889fca40af9fd4aaf6fdcfe467` 终态 `OK` 稳定，但尚无 30 次对照，不能宣称 p95 改善。

## 限制与后续

- 第八轮 DOM 轨迹中 `data-turn-answer=OK` 后 live 文本节点短时空白，直到完成交接才出现 durable `OK`；不能把浏览器 live 首字体验宣称为彻底优化完成。API SSE p95 数据仍以 `perf-thread-run-174-20260925.md` 为准。
- 真实 `Skill` 加载被 `production-orchestrator` 策略拒绝；需授权 fixture 才可验收 Skill 工具结果。
- 未进行跨 Run ClaudeSDKClient 复用；`harness.sdk.connect` 约 1.4 秒仍是首字前大头。需要独立安全设计，不能共享 Run 级 cwd/hooks/凭据。
