# 自动化线程的交互工具读取修复（174，2026-10-17）

## 现象

174（`https://172.20.109.174`）打开自动化任务「每日 AI 新闻推送」的线程时，回答里的两个交互工具卡片都显示：

> 重新加载交互工具
> 交互工具暂时无法恢复，请重试。原始文件仍可从产出中打开。

点击重新加载仍然失败；同一回答的正文和产物文件正常。

## 根因

自动化执行使用工作负载身份，session 的 `user_id` 是 `automation:{task_id}`：

```
线程      automation_automation_b29c82524a8d49cebf3ac9e2d59723fd
session   automation_session_automation_b29c..._15b6679c2ccd
session.user_id   automation:automation_b29c82524a8d49cebf3ac9e2d59723fd
线程绑定  local / user_1c16a8994ff548c298c356fd8385eb76
```

交互工具的快照接口 `/v1/ui/presentations/{id}` 使用 `require_owned_run`，它要求
`session.user_id == 当前用户`，因此对线程主人稳定返回 404。产物接口此前已经为同一场景
按 thread binding 放行，交互工具接口没有同步。

数据库里两个 presentation 都存在，且属于今天 09:00 的同一 Run，因此不是生成或发布失败：

```
ui_54a5be4106d3e7888afd7760 | run_22874666eb4b4fd0b7a26dc12e467629 | table
ui_ca311a7641d635bc879bde81 | run_22874666eb4b4fd0b7a26dc12e467629 | table
```

## 修复

- 新增共享的 `require_readable_run`：本人 session，或当前用户的自动化 thread binding；
  与产物读取同一条授权规则。`require_owned_run` 继续用于写入/取消。
- `ui` 路由的快照、HTML、动作轮询和视图状态改走该授权；动作提交仍要求 Run 所有权。
- `save_view` 增加 `allow_foreign_owner`，只在路由已完成绑定授权后使用；视图状态按读取用户隔离。
- `artifacts` 路由复用同一套 `is_automation_session` / `require_readable_run`，删除本地副本。
- 回归测试 `test_automation_thread_reads_and_restores_its_presentation`：旧代码返回 404，
  新代码返回 200，并验证其他用户仍是 404 与视图状态 CAS。

## 验证

- 本地：`tests/integration/agui/test_ui_actions.py`、`test_html_presentation.py`、
  `test_agui_ui_presentation.py`、`test_artifact_api.py` 共 35 项通过。
- 174 生产数据（服务 token + 用户身份）：
  - `ui_54a5…` → 200，`ui_ca31…` → 200
  - 另一用户 → 404
  - `PUT view-state`（expected_version=0）→ 200，返回 version=1
- 部署后 `https://172.20.109.174/` → 200。

## 部署与回滚

- 代码提交：`dee99024`（分支 `fix/ui-automation-presentation-access`，基于 `origin/develop`）。
- 174 API 叠加镜像：`kai/sapling-preview-api:174-20261017-ui-access-r1`
  （`FROM kai/sapling-preview-api:174-20261016-r1`，只替换 4 个 Python 文件）。
- Compose：`/data/kai-intelligent-ui-develop-b6289d48/compose.runtime.private.json`
  （备份 `compose.runtime.private.json.bak-ui-access-20261017`）；仅重建 `api`，worker 未动。
- 回滚：把 `api.image` 改回 `kai/sapling-preview-api:174-20261016-r1`，执行
  `docker compose -p agent-studio-174 -f <compose> up -d --no-deps api`。

## 边界

自动化线程里的交互工具现在可以正常加载、排序和持久化视图状态。卡片上的动作（表单提交/按钮）
仍走 `require_owned_run`，与产物写入/取消的既有边界一致；如果后续需要允许在自动化线程内提交动作，
应单独设计（当前 `Publication.owner_user_id` 是工作负载身份）。
