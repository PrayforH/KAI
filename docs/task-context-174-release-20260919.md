# 任务工作台体验优化：174 发布记录（2026-09-19）

## 改动（5 项，前后端各一批）

1. **主题外观快捷切换**：左下角账户菜单（AccountMenu）新增「主题外观」区块，提供
   深色主题/浅色主题单选（当前项打勾），与「个人设置-外观」共用同一个
   `agent-harness-color-mode` 存储与 `data-color-mode` 应用逻辑（`lib/color-mode.ts`
   抽为共享模块，settings 页的 ThemeSelector 同步重构复用）。
2. **「技能 / MCP」→「插件」**：任务侧栏的 capabilities 入口改名（`task-sidebar`
   的 labelOverrides 与 `workspace-navigation` 默认 label 同步），页面路由不变。
3. **任务列表整体刷新修复**：切到「插件」等 Studio 页面再返回时，TaskSidebar 重新
   挂载会导致「任务/智能体」两个列表先清空进 loading 再整体重绘。现在挂载时直接从
   `task-history` 的模块级快照播种（新增 `peekCachedTasks`），列表原地渲染、后台静默
   刷新；pin/重命名/归档后通过 `harness:task-list-changed` 事件让所有侧栏立即刷新。
4. **header 任务详情气泡 + 更多菜单**：
   - header 的项目文件夹 chip 变为触发器：悬停（约 220ms 延迟）或点击弹出任务卡片
     （任务名、agent 目录名、最近活动时间、版本绑定行——「分支信息」的对位展示，
     因为本平台任务没有 git 分支，展示的是任务固定的 agent 版本绑定）。
   - chip 右侧新增「…」菜单：置顶任务/取消置顶、重命名任务（独立输入对话框）、
     归档任务（运行中禁用，归档走确认对话框；归档当前任务后自动新建任务）。
5. **对话输入固定行数 + 下拉展开**：输入超长时折叠窗口固定约 4 行（内部滚动 +
   底部渐隐），底部居中出现圆形下拉按钮，点击展开（上限 240px）/再点收起；仅在
   文本真正溢出时出现。深浅色主题均已适配。

### 后端（置顶/重命名需要持久化）

- `core/models.py`：`AguiThreadBinding` 新增 `pinned_at`；`title_source` 扩展
  `"user"`。
- `core/ports.py` / `adapters/memory.py` / `storage/platform_repositories.py`：
  仓储新增 `set_pinned`；Postgres `list_for_user` 置顶优先排序（JSON payload 表达
  式，**无新增迁移**，绑定行本就是 JSON payload 存储，`alembic_version` 保持
  `0033`）；`update_title` 支持用户来源。
- `agui/service.py`：`set_pinned` / `rename_thread`；生成式标题（fallback/model）
  一律不覆盖用户改名。
- `agui/routes.py`：`PATCH /v1/agui/threads/{thread_id}` 的 body 从
  `{archived}` 扩展为 `{archived?, pinned?, title?}`（响应含 pinned_at/title）；
  `/threads` 列表排序置顶优先、summary 增加 `pinned_at`。

## 质量门禁

- 后端：`pytest tests/unit`（1279 通过）+ `tests/integration/agui tests/unit/agui`
  （82 通过，含新增的置顶/改名/排序集成测试）；ruff 通过（`tests` 下 4 个
  E501 为存量问题）；`git diff --check` 通过。`tests/integration/storage` 的
  失败均为本地无 Postgres（127.0.0.1:5432 拒绝连接）的环境问题，与本轮无关。
- 前端：110 个测试文件 / 713 项测试全部通过（含新增
  `tests/task-header-actions.spec.tsx`）；`tsc --noEmit`、Next.js production
  build 通过。

## 174 发布

| 项 | 值 |
| --- | --- |
| API/Worker 镜像 | `kai/axis-api:task-context-20260918`（基座 `develop-bfa9f8a` + 全量 `src/harness`，api-code-only.Dockerfile，单层无补丁链） |
| 发布目录 | `/data/task-context-20260918/`（`compose.api.release.json` = report 配置换镜像 tag） |
| Web 镜像 | `kai/axis-web:20260919-000436`（174 上按 runbook 构建） |
| 新 Web 容器 | `axis-web-20260919-000436`（3501，healthy，restart 0） |
| 回滚容器 | `axis-web-develop-680739c-rollback-20260919-000436`（保留停止态） |
| 冒烟 | 3599 临时容器：`/`=200、icon=200、auth config/runtime-config=200、session=401、/studio/spaces=404，全部符合预期后删除 |

镜像内容复核：`grep pinned_at` 在新 API 镜像内命中；Web 镜像 `.next/static` 中
含「展开输入框/主题外观/插件」chunk。`PATCH /v1/agui/threads/x`（无鉴权）返回
401（路由存在）。切换后 api + 3 worker + web 全部 `running/healthy`、重启 0。

回滚：

- API/Worker：`cd /data/task-context-20260918 && docker compose -p agent-studio-174
  -f /data/develop-bfa9f8a/compose.api.release.report.json up -d --no-deps
  --force-recreate --scale worker=3 api worker`（改回旧镜像 tag 即可，无迁移需要回滚）。
- Web：按 runbook 第 12 节，`AXIS_FAILED_CONTAINER=axis-web-20260919-000436`、
  `AXIS_ROLLBACK_CONTAINER=axis-web-develop-680739c-rollback-20260919-000436`、
  `AXIS_PREVIOUS_NAME=axis-web-develop-680739c`。

## 边界

- 未操作 173；未发送真实模型任务；未清理回滚容器与旧镜像。
- 置顶/重命名/归档的端到端浏览器操作验证需登录态，留给用户在
  http://172.20.109.174:3501 上确认（强制刷新）。
