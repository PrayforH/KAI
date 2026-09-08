# Web 对话体验与热插入发布记录

## 范围与版本

- 目标：`http://172.20.109.174:3501`。
- 计划：[逐项任务清单](plans/2026-09-06-web-conversation-experience.md)。
- 发布源码：`/data/axis-ui-20260906-004236`。
- API / 3 个 Worker / quality-sync：`kai/axis-api:20260906-004236`。
- Web 最终镜像：`kai/axis-web:20260906-004236-recovery`。
- Claude Agent SDK：部署版本由 `0.2.128` 升至 `0.2.152`。
- 本次用户明确追加了 SDK 和后端热插入，因此发布范围包括 API 与 Worker，超出既有 Web-only 手册的范围。未执行数据库迁移，未改变数据库、Redis、MinIO、模型配置或智能体版本。

## 实现

1. 成功的内部环境准备事件从主过程隐藏；标题保持稳定，区分思考摘要和普通进展，运行中显示动效和真实计时，完成耗时保留在回答尾部。
2. `/new /stop /files /clear /help` 命令、`@` 任务智能体选择与 `$` 已安装技能引用。紧凑智能体选择器统一显示版本，顶部不再重复。
3. 输入区等宽待发送横栏支持排序、编辑、删除、引导与暂停恢复；队列按用户和任务隔离。刷新后继续同步运行状态与停止按钮，恢复的队列不会自动发出。
4. `GET/POST /v1/runs/{run_id}/steer`：会话所有权及权限校验、幂等 request_id、持久事件收件箱与接收回执。Claude 在同一 client 上追加输入，Codex 使用 `turn/steer`，不新建 Run。
5. 项目列表默认 5 个，显示更多和长名称悬停；右侧分栏提供文件搜索、预览、下载、刷新和任务详情。
6. 个人设置默认隐藏内部子智能体、隐藏工作区成员入口；技能新建区分对话创建与上传到草稿。

## 验证

- 前端：71 个文件、482 项测试通过；Next.js production build 和 TypeScript 通过。
- 后端：完整回归 1097 passed / 1 skipped；追加边界案例后的专项 37 passed；Ruff、Pyright 和 `uv lock --check --offline` 通过。
- 浏览器：用隔离模拟 Runtime 验证引导回执、同轮补充、队列编辑/删除/排序、刷新保留与暂停、完成时间、分栏、技能创建入口和个人偏好。
- 部署前只读核对所有 Run 均为终态；API、Worker、quality-sync 的 Compose 环境与原容器一致。
- API 基础构建因远端无法连接 cgr.dev 被阻断。使用 `deploy/docker/api-update.Dockerfile` 基于已验证的原 API 镜像替换源码并升级 SDK；保留原 OS、CLI 与其他依赖，镜像内 `pip check` 通过。
- 正式环境只做 HTTP、鉴权和健康检查，没有发送收费模型任务。

## 使用边界

热插入支持文本；带附件的补充排队在下一轮执行。只有运行时输入通道就绪才开放引导，收到持久回执后才移出横栏。队列存储在当前浏览器，刷新后手动继续，不跨设备同步。显示运行时提供的思考摘要，不构造隐藏思考。

## 回滚

原 API 镜像 `kai/axis-api:20260905-091037` 与原 Compose override 保留。在 174 执行：

```bash
cd /data/agent-studio/docker-compose
docker compose --profile observability --env-file .env.production \
  -p agent-studio-174 -f compose.yaml -f compose.harbor.yaml \
  -f compose.axis-release-20260905-091037.yaml \
  up -d --no-deps --no-build --scale worker=3 api worker quality-sync
```

原 Web 回滚容器：`axis-web-20260905-091037-rollback-20260906-004236`。最终验收后保留，不清理旧镜像与源码。

```bash
docker stop axis-web-20260906-004236-recovery
docker start axis-web-20260905-091037-rollback-20260906-004236
```

回滚前核对 3501 的实际占用容器；回滚后检查 `/healthz`、首页与容器健康。

## 健康检查记录

- Web：首页、图标、鉴权配置、runtime config 均返回 200；未登录 session 返回 401，符合预期。
- API：`/healthz` 返回 200。API、3 个 Worker、quality-sync 均 healthy，重启次数 0；Worker 内 SDK 确认 0.2.152。
- 保留首次发布和视觉验收过程中的 Web 镜像用于排查；正式使用上文 recovery 镜像。
- 另外修复了刷新后终态先于正文发布的时序问题，先导入完整历史再结束恢复轮询。

最终镜像 ID：

- Web：`sha256:98b58a28dc84157f2c4bbe2c674413a7f0399f918fc97d19b2845b7927c3463a`
- API / Worker：`sha256:ef6520e9c1a265f7121068e31a6ce2cab6f51a72359416ba36eff548d67955c4`

正式切换完成，以上 6 个服务均 healthy / restart 0。临时 3599 Web 和 18801 API 容器已移除；本地模拟 API、Web 和测试 PostgreSQL 已停止并清理。浏览器模拟任务确认刷新后的运行自动恢复完整终态正文与耗时；远端桌面浏览器打开 174 超时，正式站点验收依据为服务器 HTTP 与容器检查。
