# 全平台取消费用和 Token 执行限制

用户范围：整个 agent-studio 的所有主智能体、子智能体和历史版本。用量仅用于观察，不因费用、Token 或子智能体 Token 额度终止执行。保留权限、路径隔离、超时及并发等资源保护。

故障：run_0b7b67acc8204f599f6f1969e8313fa1 在 2026-09-07 11:37:57 +08:00 返回 error_max_budget_usd，SDK total_cost_usd=4.005826，已发布版本额度为 4。两个 Agent 调用请求了未声明的 general-purpose / claude，被 policy_denied；已声明角色只有 fact-researcher、audience-analyst、industry-analyst，均绑定只读 helper-agent@1.0.0。

- [x] 统一取消 SDK 预算参数、平台模型配额及子任务 Token 拦截；兼容历史发布版本。
- [x] 从发布角色生成明确的委派名称/工具能力提示，拒绝未知角色时返回可用名称。
- [x] 修复工具拒绝后子任务仍显示运行中，以及历史预算失败的错误展示。
- [x] 回归验证，部署 174 的 API/Worker/Web，确认有效运行选项与页面历史回放。
- [x] 记录结果与可回滚版本；不覆盖历史任务状态，不自动重新运行用户业务。

发布完成：20260907-115304。详见 ../unmetered-agents-174-release-20260907.md。
