# 174 自动化产物读取修复合并与验证（2026-09-26）

## 版本与范围

从主工作区仅提取 `src/harness/agui/service.py`、`src/harness/api/routes/artifacts.py` 和 `tests/integration/test_artifact_api.py` 的未提交修复，干净应用到 174 治理版独立工作树；代码提交 `a4d7329047ddb8de599d3bdba6e151f44b1a18d9`。未合入主工作区其他未提交改动。仅更新 174 API，三 Worker、3501 Web、3301 Web、数据库均未改动。

## 原因与修复

“每日 AI 新闻推送”任务的 PPT 实际由会话内人工 Run `run_6928e4fd8b154d6fa46153f712c418ef` 生成并发布：`artifact_9fb5393db5054d4a910e5084e6819632`，READY，105,607 字节。任务绑定属于用户，执行 Session 的 user_id 是 `automation:<task_id>`；旧产物索引再次按 Session user_id 排除了它。修复对读取路径要求同租户、当前用户任务绑定与自动化 workload identity/api_key_id 一致；上传和取消仍保持严格 Run 归属。

## 测试

- 产物 API、自动化服务、AG-UI 读取、仓库契约：28 passed。
- 后端全量单元+集成：1853 passed、8 skipped；6 个既有弃用/测试提示。
- Ruff、产物路由 Pyright、`git diff --check` 通过。对整个 `agui/service.py` 做严格 Pyright 会报告原有 `set_project` 仓库协议类型问题，新增方法没有该错误。

## 174 发布与真实只读验证

- 发布前 API/三 Worker 为 `kai/axis-api:governance-64896583`，无活跃 Run。新 API 镜像 `kai/axis-api:governance-artifact-a4d73290`，image ID `sha256:23d523621cabfd03ede05142455154e2bc29ffad2530f951d2ec9156f93d345b`，架构 amd64，revision label 为代码提交。
- 仅 Compose `api` 镜像一行切换。API healthy，`8800/healthz` 200；三 Worker 容器 ID 在切换前后逐字节相同，仍运行治理镜像。3501 owner 仍为 `axis-web-governance-64896583`，数据库迁移头 `0037`。
- 服务端使用 API 容器现有服务凭据进行只读 HTTP 检查（未输出凭据）：当前用户 `GET /v1/artifacts?thread_id=<news-task>` 返回 200、3 个文件，包含目标 PPT；下载 200、105,607 字节；按 Run 列表 200、1 个文件。
- 其他用户和其他租户下载 404；错误任务绑定下载 404；其他用户按 Run 列表及任务目录 404；任务所属用户也不能取消该自动化 Run（404）。
- 回滚：174 `/data/releases/artifact-owner-a4d73290/compose.pre-switch.yaml`，SHA256 `ed3bc8b1535895a2bfab7f9491badb2ce5c37d9df1ced7a53d751b817ad4b6ce`。在无活跃 Run 时恢复该文件到 `/data/agent-studio/docker-compose/compose.deepagents-174.yaml`，用当前 `compose.yaml`、`compose.harbor.yaml`、`.env.production` 对 `agent-studio-174` 只执行 `up -d --no-deps --no-build --wait api`。无需数据库回滚。

浏览器视觉验收未执行；API 已给文件侧栏所用的索引返回目标 PPT，页面仍需使用有效登录会话刷新文件目录。
