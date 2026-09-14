# WeKnora / DeepAgents 分支补齐与顶部版本入口

2026-09-14。WeKnora 与 DeepAgents 的全部已提交实现均已进入 develop；导出功能位于同一条 `feature/weknora-knowledge-base` 分支，并没有遗漏的独立 DeepAgents 远端分支。`auto/model-management` 也已经是 develop 的祖先。

## 线上与源码差异

develop 侧的 `3d8a5fc` 删除了顶部版本组件；feature/weknora-knowledge-base 仍保留该入口。173 当前镜像 `agent-studio-web:deepagents-export-20260914` 的静态产物 `3ueyb9y4sz0tc.js` 仍包含 `lead-agent` 以外的 `kind:"version"` 入口。因此 173 与 feature 分支一致，不能用 develop 的提交推断 173 页面已删除。上一轮合并保留了 develop 的删除，未将这处页面行为对齐。此次恢复主对话顶部的版本选择：lead-agent 不显示，其他智能体显示；复用既有版本刷新、任务绑定和运行中锁定逻辑。

前端 89 个测试文件、590 项通过，1 项跳过；生产构建通过。离线 Dockerfile 在 173 上以 `--network=none` 完成原生构建，两份归档校验和无 NODE_PATH 的 Office 模块加载通过；仅构建测试镜像，未替换线上容器。

## 其他必要改动

- 模型配置分支已经合入。9 月 13 日的模型保存 500 来自 173 历史草稿行与 JSON revision 不一致，已在数据库修复；不是尚待合并的模型实现。归档 [修复记录](model-route-save-173-20260913.md)，不改变模型 ID、端点、凭据或生产模型选择。
- Office 技能使用的 Node 模块必须能在运行环境不传 NODE_PATH 时被找到。在线增量 Dockerfile 补充 `/node_modules` 并验证；离线部署拆成 `api-update-offline.Dockerfile`，保留两个归档的 SHA256 校验与 SDK 版本检查，避免原工作区改动让既有在线构建强制依赖缺失的本地归档。
- 收录已有 `api-code-only.Dockerfile`。只可用于基础镜像已经具备新代码所需依赖的更新；依赖、数据库或技能资源变化不能仅靠它发布。
- 收录 DeepAgents 运行时方向分析，具体工程导出方案以 [合并审查](deepagents-export-develop-review-20260914.md) 为准。

## Tavily MCP 评估

不原样合入工作区当前的“平台全局退役”补丁：

1. 它会按 `tavily-readonly` 名称移除所有目录条目，包含个人副本，并清除执行档案授权。
2. 草稿加载时自动剥离引用；运行时忽略已发布不可变版本的工具绑定，导致历史 Agent 能力静默改变。
3. 它还移除了 WebSearch 复用既有 MCP key 的回退，可能使只配置过该凭据的用户失去搜索能力；这超出了“默认不再提供 Tavily MCP”。
4. WeKnora 与 DeepAgents 导出均不要求平台全局退役 Tavily。内置 WebSearch 的 Tavily REST provider 与 Tavily MCP 是不同的配置入口。

建议后续若确认停用，只停止新增默认绑定，并按实际 owner、草稿与已发布版本引用做迁移；个人配置及历史固定版本应给出明确的替代路径。此次不清理数据库、凭据、个人 MCP 或已发布工具；原退役补丁继续留在原工作区，未丢弃。
