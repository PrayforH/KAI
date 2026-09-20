# 174 项目新建任务与侧栏修复（2026-09-20）

## 完成范围

- 将 174 已部署的项目功能与界面基线 `f9af1db2` 合入 develop，避免发布退回旧的智能体分组侧栏。
- 项目右侧“＋”改为传递项目实体，空项目也能使用。在服务端创建 `idle` 空任务，并同时保存项目归属，不触发模型运行。新任务立即打开并展开所属项目；刷新后仍可见。
- 管理页面的同一入口改为创建并打开新任务，修复原先跳回已有任务的接线。
- 创建期间阻止重复点击，失败显示可重试提示；任务列表变更清除缓存，并防止旧请求覆盖刷新结果。
- 顶部品牌栏与对话 header 共用 46px 高度；导航 32px、任务 30px。导航图标、项目图标和普通任务标题统一从左侧 16px 开始。项目子任务保持小幅缩进。项目和任务共用一个滚动区，账户栏固定底部，手机任务行保留 40px 点击高度。
- 真库验证发现并修复项目删除时 JSON/JSONB 空值写入错误，同时补齐新任务的 `project_id` 索引列写入，确保删除项目后任务归属能正确清空。

## 接口与权限

`POST /v1/agui/threads` 接收 `project_id`、智能体名称/版本及可选 owner/space，返回 201 和完整任务摘要。沿用 `tasks:write`、项目归属校验及团队智能体授权。不存在/他人的项目返回 404，归档项目返回 409。新任务不创建 Run。

## 验证

- 前端：114 个测试文件，744 passed、1 skipped；包含空项目新建、防重复点击、错误重试和旧列表请求竞态回归。
- 后端 AG-UI/项目：97 passed（`--import-mode=importlib`，避免既有同名测试包冲突）。
- PostgreSQL：隔离临时 pgvector/PostgreSQL 18 数据库上的项目清理回归 1 passed，验证保留其他项目、其他租户和原有任务标题。未在业务库运行建表/清表测试 fixture。
- 本地 Next.js production build（webpack）通过；最终 Linux amd64 镜像的标准 `next build`（Turbopack）通过。
- 真实 174 API：连续创建两条独立空任务，归属持久化、跨用户 404、归档项目 409、删除项目后解除归属均通过。
- 174 浏览器：深浅主题/窄屏布局与入口契约检查通过；3501 上接真实 API 的项目“＋”、刷新恢复、管理页入口均通过，无 page error。验证上下文使用服务授权；未测试密码登录，也未变更用户登录会话。
- 验收数据：测试任务已归档，测试项目已删除；没有发送真实模型任务。

## 部署

发布目录：`/data/project-sidebar-20260920`。

| 服务 | 当前镜像 / 容器 |
| --- | --- |
| API | `kai/axis-api:project-sidebar-20260920-final` / `agent-studio-174-api-1` |
| 3501 Web | `kai/axis-web:project-sidebar-20260920-v2` / `axis-web-project-sidebar-20260920` |

API 在现有 `deepagents-174-4f95e64b` 镜像上只更新 AG-UI routes/service 与 platform repositories 三个文件，保留既有运行时。发布前比对 compose 与运行容器环境，差异为零。Web 从已合入 develop 的完整前端源码构建为 linux/amd64 镜像后传输到 174。API /healthz 与 Web / 均 200，容器均 healthy。

只切换 API 与用户入口 3501；3301、Worker、数据库、中间件和模型配置未更新。当前镜像保留在 174 本地，未推送 Harbor；源码未推送远端 Git。

## 回滚

- Web：停止并移除 `axis-web-project-sidebar-20260920`，将停止态的 `axis-web-f9af1db2-rollback-project-sidebar-20260920` 恢复命名为 `axis-web-20260920-f9af1db2` 并启动。
- API：将发布目录中的 `compose.deepagents.before.yaml` 恢复到 `/data/agent-studio/docker-compose/compose.deepagents-174.yaml`，使用原 compose.yaml + compose.harbor.yaml + compose.deepagents-174.yaml 组合执行 `up -d --no-deps --wait api`。
- 本次修复没有新增数据库迁移；0035 是本次合入的既有项目模块迁移，174 原本已应用。
