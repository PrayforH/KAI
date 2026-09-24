# Builder 单对话运行内工具（2026-09-24）

## 部署与范围

173 API、Worker、Web 均为 `a84fbb54`，容器 healthy，Web HTTP 200。适用已有草稿的 Claude Agent SDK 与 DeepAgents Builder。174 与 Codex runtime 未改动；新建草稿的引导仍保留原路径。

借鉴 Agenta Build Kit 的做法，在当前运行中提供配置工具，由模型按任务选择调用，而不是先进行独立意图分类。保留一个输入框与共享的输出、工具过程、附件链路。显式试跑仍使用草稿当前配置；可编辑 Builder 消息直接进入带配置工具的 try-run，不先调用 builder-conversation 或单独读取附件。

参考代码：[Agenta build_kit.py](https://github.com/Agenta-AI/agenta/blob/e502a0126b3fcaf26df6c2c128d2779b73666883/api/oss/src/core/workflows/build_kit.py)。这是借鉴工具注入与同轮执行结构，不是复制 Agenta 全部实现。

## 行为与边界

- read_configuration 延迟读取当前草稿、能力目录和变更 schema，普通任务不付出目录读取和额外模型请求的开销。
- propose_configuration 复用既有校验，写入 builder.proposal 事件并展示修改卡片。工具自身不保存、不发布；应用时仍校验权限与草稿版本。
- 工具绑定服务端确定的用户、租户、草稿及修订。模型无法通过工具参数选中另一草稿；共享草稿需要编辑权限。
- 技能永久停用生成 removeSkills；“仅本次不用”只影响本次行为。启用联网保留其他内置工具。取消建议通过专用事件撤销待审卡片。
- Skill Creator 子任务在当前 Worker 内执行，避免父任务占用所有 Worker 等待队列子任务；保留既有生成与测试产物校验。
- SDK 工具通过原有双向 MCP 控制通道连接；DeepAgents 使用相同工具 handler。Daytona 桥接有单元覆盖，本轮未单独实测该供应商。

## 自动化验证

- 前端全量：917 通过、1 跳过；Next.js production build 通过。
- 后端相关回归分批执行：102、57、27 项通过，批次有重叠，不能相加作为去重测试总数。
- 覆盖版本冲突、权限隔离、配置读取前置要求、只提案不修改、显式应用、普通运行无工具注入、运行模式隔离、SDK 工具门禁、DeepAgents 工具门禁、远程 SDK descriptor 序列化、Skill Creator 子任务执行。
- Ruff 与 git diff --check 通过。

## 173 真实模型验证

使用独立 member QA 草稿和惰性技能，不触碰用户“资料研究助手”。原始精简结果见 [runtime-tools-a84fbb54.json](builder-173-20260924/runtime-tools-a84fbb54.json)。

| 运行时 | 场景 | 结果 | 单次端到端耗时 |
|---|---|---|---|
| Claude Agent SDK | 普通问题 | 直接回答，无配置工具 | 3.54 秒 |
| Claude Agent SDK | 启用联网 | read + propose；QA 应用后仅增 WebSearch/WebFetch | 9.93 秒 |
| Claude Agent SDK | 移除 archify | 仅生成 removeSkills，保留 qa-preserve | 6.99 秒 |
| Claude Agent SDK | 取消待审建议 | discard 事件，草稿不变 | 6.57 秒 |
| Claude Agent SDK | 本次不用技能 | 回复收到，无配置修改 | 3.43 秒 |
| DeepAgents | 移除 archify | read + propose，草稿不变 | 9.32 秒 |

所有运行 succeeded；所有提案应用前草稿保持不变。耗时含客户端轮询粒度，不是严格 A/B 测量，不代表主对话或所有模型都会达到此速度。此改动移除了 Builder 前置模型调用，没有实现运行进程预热复用，也没有减少模型自身推理步骤。

## 浏览器复验

Zcode 浏览器验收未完成：旧登录态过期，初次脚本停在登录页，尚未发出模型运行；随后 Zcode 当前模型 OpenAI/DF-6A 报 An internal error occurred，修正登录与验收判据的请求未执行完成。未将此记为产品失败，也不能以 API 验证代替卡片可见性及点击应用验证。前端单元测试已覆盖工具提案卡片与应用，仍需完成浏览器实测。

用户随后手动重新触发 Zcode，并明确要求后续测试由用户手动完成。本轮停止操作 Zcode/浏览器；浏览器验收状态保留未验证，不声称通过。
