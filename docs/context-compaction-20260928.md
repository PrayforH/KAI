# 会话上下文与实际压缩

基线：2026-09-28 拉取的 develop `7ea54db6`。实现分支：`auto/context-compaction`。

## 原有行为

AG-UI thread 绑定平台 Session，每条请求创建 Run。浏览器历史消息不等于模型实际收到的历史。

- Claude 通过 SDK session id 续接，SDK 管理原始 transcript 和原生自动压缩。
- Codex 通过 app-server `thread/resume` + `turn/start` 续接。正常续接只发新输入；原生线程恢复失败时另建线程，并注入恢复投影和有限的历史用户请求。
- DeepAgents 每个 Run 新建 graph，没有 checkpointer。原注释声称重放历史，但 graph 输入实际只有本轮 prompt；既没有前轮助手回答，也没有持久化原生摘要。
- 平台 Context Digest 只从最近成功 Run 截取用户请求和助手结果，各最多约 950 字符，并记录产物与工作区引用。这是确定性的恢复投影，不是对完整历史调用模型做摘要。
- 原“压缩上下文”按钮实际是 rebase：克隆新的 Session，并带入最新 Digest。原 Session 可切回，但这个 Digest 不能保证保留所有早期约束。
- 原窗口策略的 65%/75%/85% 是展示和建议阈值，没有调用模型执行压缩。

## 本次实现

### 发布配置

新增 `spec.context`，经过 Studio 草稿、发布 Manifest、Bundle 导入和 DeepAgents 项目导出。前端“运行与权限”提供对应输入；切换运行时会清空不兼容的配置。旧版本省略这个字段仍可加载。

Codex 示例：

```yaml
spec:
  runtime: codex-app-server
  context:
    contextWindowTokens: 128000
    autoCompactTokenLimit: 64000
```

运行时下发 `model_context_window=128000` 和 `model_auto_compact_token_limit=64000`。窗口覆盖不是扩大模型真实能力的办法；应采用实际模型支持的值。阈值必须低于显式窗口，最小 1024 token。留空使用 Codex 的模型默认值。

Claude 示例：

```yaml
spec:
  runtime: claude-agent-sdk
  context:
    autoCompactPercentage: 70
```

运行时下发 `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70`，范围 1–95。留空使用原生默认值。该百分比由 Claude 原生上下文计算，不能直接用平台的字符数或累计计费 token 替代。

DeepAgents 示例：

```yaml
spec:
  runtime: deepagents
  context:
    autoCompactTokenLimit: 32000
    keepRecentMessages: 12
```

使用 DeepAgents 0.7.13 的真实 `SummarizationMiddleware`：调用摘要模型、替换后续模型请求里的旧历史、保留近期消息，并通过 sandbox backend offload 被归纳的内容。摘要使用当前已解析的模型与凭据；本次没有新增单独的摘要模型路由。

触发阈值留空时采用中间件的模型配置默认值；近期消息默认保留 20 条。这里采用中间件的近似 token 计数，不在界面冒充模型返回的精确用量。保留窗口必须允许切出旧消息；单条超长输入或过大的近期消息集仍可能超限，压缩不会扩大模型物理窗口。

这些配置控制自动压缩，不是费用配额、最大模型轮次或长期记忆开关。本次没有增加关闭原生自动压缩的选项，也没有把平台 rebase 按钮改造成三个供应商通用的即时 `/compact`。

### DeepAgents 多轮恢复

```text
Run 开始
  → 按 tenant + Session 读取最后的 context.history.checkpoint
  → 恢复用户/助手对话及已有摘要
  → 追加本轮请求，以多条有角色的消息送入 graph
  → 原生中间件根据阈值生成摘要，后续请求使用摘要 + 近期消息
  → graph 成功完成后，保存有效历史 checkpoint
  → 下一 Run 继续读取这个 checkpoint
```

checkpoint 从原生 `_summarization_event` 的 summary/cutoff 和最终 graph state 构建，不是把完整旧历史和摘要一起再发回模型。只保存可见的用户/助手文本；不把隐藏 reasoning、原始工具参数或孤立的 tool result 写入对话 checkpoint。工具消息在同一 Run 内仍由 graph 使用；跨 Run 的工具审计信息保留在既有事件记录中，归纳过的历史另有 offload 文件。

没有 checkpoint 的旧会话，首次从该 Session 已成功的 Run 输入及最终助手事件重建角色消息，不信任浏览器提交的助手历史。此迁移读取最多 1000 个 Run，超过上限明确失败，不静默截取。之后每轮直接恢复 checkpoint。

摘要失败或返回空文本时，不发布替换 checkpoint。已保存的历史仍可用于下一次执行。checkpoint 在 graph 成功后、运行时结果事件前发布；它表示模型执行已完成，不等同于之后工作区归档等步骤全部完成。

### 可观察性与界面

- Claude `compact_boundary`、Codex `contextCompaction` 完成项、DeepAgents 原生 summary state 统一映射为 `context.compacted`。
- 事件只保存时间、运行时和可获得的数量信息，摘要正文不作为压缩状态展示。
- Context API 返回最近一次实际压缩；面板显示完成时间。
- Codex `tokenUsage.total` 用于累计用量，`tokenUsage.last` 用于当前上下文窗口，避免压缩后计量仍随历史累计上升。
- 原 rebase 功能保留并改名为“从恢复点重建”，明确它可能只携带最近一轮的信息；原会话仍可切回。

## 与 Codex 的对应关系

Codex 的关键不止是有一个阈值配置，而是触发后构造 replacement history、替换活跃会话历史并持久化，使恢复后的模型继续读取同一个压缩结果。其本地摘要路径与服务端 compaction 路径都是对实际模型输入生效。平台集成采用原生机制，避免再叠一套只改 UI 用量、没有改变模型输入的“压缩”。

参考：[Codex 配置说明](https://learn.chatgpt.com/docs/config-file/config-reference)、[OpenAI Compaction](https://developers.openai.com/api/docs/guides/compaction)、[Claude 环境变量](https://code.claude.com/docs/en/env-vars)。Codex 源码核对基于 main `1cc7e236` 的 `codex-rs/core/src/compact.rs` 和会话执行代码；不要把该源码快照等同于所有生产客户端版本。

长期记忆仍是独立系统：本次压缩只服务于同一 Session 的连续任务，不把临时对话摘要自动升级为用户长期事实。

## 验证与边界

本次最终回归：后端相关测试 **529 passed**（包括真实 DeepAgents 导出项目执行与 wheel 打包）；前端相关测试 **92 passed，1 skipped**；TypeScript `tsc --noEmit` 与改动 Python 文件 Ruff 检查通过。新增上下文模块、配置和窗口策略的定向 Pyright 检查通过。较大既有运行时文件仍有基线已存在的第三方类型等诊断，未将其宣称为全仓库类型检查通过。

- 真实 DeepAgents/LangGraph 中间件 + 可控测试模型：超过阈值时实际调用摘要模型；旧长文本从后续请求中消失；重要事实和近期消息保留；下一轮读取相同压缩结果；摘要流不混入最终回答；空摘要不覆盖状态。
- Worker：从服务端事件重建角色消息，优先采用压缩 checkpoint，其他 Session 的内容不可见。
- Native adapters：Claude 环境变量、Codex app-server 配置实际下发；两者压缩通知映射及 Codex 当前窗口/累计用量分离。
- 配置：草稿发布、Bundle 导入、导出项目、前端序列化与切换运行时覆盖相应回归。
- 未运行生产模型评测，未部署；真实摘要的事实保留率、成本、中文质量及供应商兼容性需在目标模型上另行验证。原生运行时自身的压缩失败处理仍由对应 SDK/CLI 决定。
