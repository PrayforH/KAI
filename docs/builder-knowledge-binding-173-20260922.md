# 173：Builder「文件与知识」展开为知识库勾选

2026-09-22 部署到 173 演化栈（`agent-evolution-173`，3302）。源码提交 `72936dcf`，
web 镜像 `kai/axis-web:evolution-knowledge-72936dcf`。api / worker 未动（`evolution-d6bada40`）。

## 问题

Agent Builder「配置」面板里的 **文件与知识** 那一行显示着 `N 项知识引用`，点开却跳到
**Tools 与联网**（`onEdit("capabilities")`）。那一段里只有公开联网、Python 算子、MCP 卡片，
**没有任何知识库勾选**；草稿的 `knowledgeReferences` 在整个 Builder 里都没有编辑入口，
只能看数量（`agenta-configuration.tsx` 与试跑页的只读展示）。

## 改法

- `KnowledgeBaseList`：把「搜索 / 勾选 / 清除 / 空态 / 失败重试」这一段抽成**接 props** 的列表。
  两个面各绑各的——对话绑定线程，Builder 绑定草稿——列表本身不持有绑定。
  `KnowledgeBasePicker` 变成「该列表 + 线程上下文」的薄包装（对话侧不变）。
- 新增 `knowledge` 配置段：勾选即 `updateDraft({ knowledgeReferences })`，与 MCP 卡片同一条
  「先改本地、保存才落库」的路径；面板上的 `N 项知识引用` 随勾选实时变化。
- **文件与知识** 行改为 `onEdit("knowledge")`；作用域里的「配置智能体知识库」也指向这一段
  （原来落在 tools 段）。

## 验证

| 检查 | 结果 |
| --- | --- |
| 本地全量前端测试 | 131 文件 / 828 passed，1 skipped；`tsc --noEmit` 干净 |
| 行 → 段落 | `tests/agenta-configuration.spec.tsx`：点该行触发 `onEdit("knowledge")`，并显示 `2 项知识引用` |
| 列表本身 | `tests/knowledge-base-list.spec.tsx`：勾选/取消/计数、清除按钮随绑定禁用、空目录与空搜索文案区分、加载失败可重试 |
| 173 实机 | 浏览器打开 `3302/studio/agents?draft=draft_396ab…`：点「文件与知识」后弹出面板标题「文件与知识」，说明文案、`知识库 · 已选 0 个`、搜索框、`暂无可用知识库`、清除按钮均在 |
| 镜像自检 | `node -e 'require("rxjs"); require("tslib")'` OK；web healthy、3302 首页 200；api/worker 未重启 |

## 这套栈里看不到勾选行——因为一个知识库都没有

| 环境 | knowledge_bases |
| --- | --- |
| 173 演化栈（3302） | 0 |
| 174 | 2 |

所以列表呈空态（`暂无可用知识库`）。要实际点选，需要先在这一栈里建一个知识库，或把这份
Builder 改动也放到有知识库的 174 上（174 当前只有对话侧那份改动）。

## 回滚

`/data/agent-studio-evolution-20260920/backups/knowledge-72936dcf/compose.json`；
把 `services.web.image` 改回 `kai/axis-web:evolution-add-icon-d1cc27f9` 后
`docker compose -f compose.json up -d --no-deps web`。
