# Codex 混合时间线对照与 174 思考碎片修复

日期：2026-09-29。范围：174；173 不变。

## 官方协议与本项目差距

来源：[Codex App Server：Items / Item deltas](https://learn.chatgpt.com/docs/app-server#items)，2026-09-29 查阅。
这是公开协议的结论，不代表已审计 Codex Desktop 私有 UI 实现。仓库中的
`web/codex-web` 是第三方快照，不作为官方桌面端实现的证据。

Codex 按 Thread / Turn / Item 组织输出。同一 Item 的 started、delta、completed
共享 ID；completed 中的完整 Item 是权威最终状态。agentMessage 可带 commentary
或 final_answer phase；reasoning 的 summaryIndex / summaryPartAdded 标记摘要分段；
工具 Item 独立维护状态和输出。Item 完成与 Turn 完成是不同生命周期。

当前 AXIS：

| 内容 | 已有能力 | 差距 |
| --- | --- | --- |
| SDK 思考 | Run 内唯一 message serial + block index | 工具回调插入时，UI 又按事件边界拆段，导致同一块被切碎 |
| 工具 | tool_call_id 关联请求和结果 | 应继续按调用更新原位置，不能将结果视为新段落边界 |
| 正文 | message.delta 流式显示；后续操作出现时转为过程说明 | 文本身份和 phase 不完整，仍依赖后续事件推断归属 |
| Codex 适配 | 思考摘要保留 item_id | agentMessage delta 丢掉 itemId；reasoning 摘要未保留 summaryIndex；文本 Item 生命周期与 phase 未完整投影 |
| 结束 | 前端 textComplete 与 Run 终态已分离 | 后续统一 Item 模型需保留该区分，不能让收尾任务延长正文光标 |

证据入口：`src/harness/runtime/codex_protocol.py::map_codex_notification`、
`src/harness/runtime/claude_sdk.py`、
`web/harness-console/src/components/activity-summary.tsx::commentaryNodes`、
`web/harness-console/src/lib/process-boundary.ts`。

## 本次修复

修复提交：5a77f1d6。只改 Web 投影，识别 Harness 已生成的稳定 SDK 思考 ID，
同块的迟到 delta 更新首次出现的行，不因夹入工具回调或正文而新建行。
不同消息序号仍分开；无 ID / 不保证唯一的旧 provider ID 保留原边界兼容行为。
历史与实时记录共用此规则，展开状态和全文保留。

真实记录回放：1,357 个事件，其中 1,298 个思考 delta、8 个唯一 SDK 思考块，
渲染为 8 行；逐块展开文本与原始 delta 拼接完全相等。原始私有记录不入库。
5 个前端测试文件、60 项测试通过，另补一项思考 / 工具 / 过程说明 / 最终正文混排测试通过。
生产 webpack 构建及 TypeScript 检查通过。

## 后续统一模型建议（尚未实施）

保留现有 Thread / Run 调度，在 Run 下增加统一内容 Item；无需为显示问题重做 Run 或沙箱生命周期。
字段至少包括 run_id、item_id、kind、first_sequence、status、parts；message 可有 phase，
工具通过 tool_call_id 关联，reasoning 部分使用 part_index 区分摘要段。

投影以首次开始顺序固定位置，delta 只更新所属 Item，completed 校准该 Item 的完整内容。
思考可折叠、工具可分组、过程说明正常显示 Markdown、最终答复显示正文；这些是同一份有序 Item
列表的不同视图，而不是互相移动的字符串。正文流结束停止光标，Run 终态停止任务状态。

迁移顺序：先在 adapter 中保留身份、phase、分段及生命周期；再统一服务端历史和前端实时 reducer；
最后切换渲染。没有 phase 的 Claude / 旧历史需要明确兼容规则，不能伪造官方 final_answer 语义。
验收必须覆盖工具回调交错、并行工具、正文后继续调用工具、流断开重放、历史刷新、错误/取消、
重复事件、权威完成快照以及展开状态稳定。

## 部署结果

用户澄清“整体替换”指 Web / API / Worker 全部更新，不是实施上述统一 Item 模型。
未进行统一 Item 模型改造。174 已部署同一源码版本 `55c60dd4`：

- API 与 3 个 Worker：`kai/axis-api:unified174-55c60dd4`，
  `sha256:d58bc3c885fd8dddd9eeedda28b778d1c0c5795f833fda45bb8273247410e1a5`。
- Web 3301 / 3501：`kai/axis-web:unified174-55c60dd4`，
  `sha256:f147de1d80b12a943218e3da7fa5fe54f276a89d7d54c10deaf2d25d0a7c5107`。
- Web BUILD_ID：`yZRX3pBGtWrlZ7Oh2W-zV`；6 个目标容器均 healthy，RestartCount 0。
- 打包时校验了 337 个 Python 源文件哈希；依赖与基线一致，SDK 0.2.152。
  数据库仍为 0037，不执行迁移。正式替换 API/Worker 前确认无活动 Run。
- 完整对话验收 `run_556667a7b5894b3bad2f07412bb96360` 返回 `OK` / RUN_FINISHED；
  首段 3886.1ms，总耗时 4495.72ms。新会话单次部署冒烟，不作为稳定性能结论。
- 发布目录 `/data/releases/unified174-55c60dd4` 保存配置备份及回滚脚本；
  旧镜像和 3501 容器保留。临时 canary 已移除。173 未修改。
