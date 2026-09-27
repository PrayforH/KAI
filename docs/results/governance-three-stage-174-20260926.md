# 三阶段权限治理简化：174 灰度验证（2026-09-26）

## 版本与范围

- 干净独立工作树基于 `63961e74`（174 原 API/Worker 镜像源码），发布提交 `64896583ece9be29a26a294b916e13b1d303f11e`。
- 仅 174 API、三 Worker 与独立 3501 Web 更新。3301 Web 未修改；173 未操作。
- 无数据库迁移，174 `alembic_version` 仍为 `0037`。

## 三阶段交付

1. 权限判定：显式 deny 优先；Run 生成文件的继续写入仅可覆盖隐式拒绝。Claude SDK `Skill` 调用须命中当前主体发布快照的技能名，父/子名册分开；静态 standard 与 orchestrator 的委派能力分层。未知工具继续默认拒绝。
2. 生产晋升证据：必须持有有效 READY Preview 和完整 Preflight，其包、执行档、政策修订/哈希与发布版本及当前有效政策一致。无 MCP 的预检可跳过 MCP 阶段，其余阶段必须通过。Snapshot 存无敏感内容的晋升时证据字段，历史 Snapshot 可读取；Builder 的生产部署操作传递匹配的 Preview ID。此证据是**晋升时核验**，并非运行时策略钉住；Worker 在 Run 开始时仍解析当前已发布策略。
3. Builder 普通路径展示有效执行/权限摘要，手动覆盖和治理编辑折入高级/管理员入口；保留已有草稿选值和服务端编译闸门。

## 本地门禁

- 后端单元+集成：1849 passed，8 skipped；6 warnings 为测试依赖/弃用提示。
- Web Vitest：144 文件，922 passed，1 skipped；Next 16.3.3 生产构建与 TypeScript 通过。
- Ruff 及变更模块 Pyright：通过；`git diff --check` 通过。完整 `api/dependencies.py` 严格 Pyright 有该文件既有的未标注类型问题，变更模块 0 errors。

## 174 发布和证据

- 发布前：API/三 Worker `perf-20260925-history-async`（源码 `63961e74`），3501 Web `develop-20260924-6d28480c`；无非终态 Run。
- 备份：`/data/releases/governance-64896583/`（mode 700），包含原 Compose/env/container inspect 及 98 MiB 的 PostgreSQL custom dump。原 Compose SHA256 `b5b44923bc618c5a0537418a6e507a841ace0c782f5e3b9baea0b3d54b5985ab`。
- 在 174 `linux/amd64` 上以前述现行镜像为基座叠加构建：API/Worker `kai/axis-api:governance-64896583`，image ID `sha256:8fc978ce41333ff2465d0763291b15a8b529c4cd18e880941c472e06fcf1e268`；Web `kai/axis-web:governance-64896583`，image ID `sha256:793d4dfe67980a90682d18f4ebd3e0e29eb9953ce70c657cdeba14a3be32eaa9`。
- 隔离 3599 Web canary `/`、`/login` 为 200，随后删除 canary；新 API/三 Worker 全部 healthy，3501 唯一 owner 为 `axis-web-governance-64896583`，旧 `axis-web-card-6d28480c` 保留停止状态。3301 原 Web 仍 200。
- 3501 Web BUILD_ID `BUIqosbPhQ7-0JXU8Y52E` 与本地一致；3501 `/`、`/login`、8800 `/healthz` 均 200。数据库 `0037`；API/Worker 镜像摘要四者一致。线上容器内 `production-orchestrator` 的 `Skill` 规则为 allow、未知工具 deny、standard 的 Task deny、显式 deny 不被高优先级 allow 覆盖。
- 无同名已发布租户治理政策覆盖 `production-orchestrator`；未执行真实模型 Skill Run 或生产晋升。模拟/预检/运行时端到端视觉及行为尚未在线上验收，不能仅以健康检查证明。

## 回滚

- API/Worker：恢复备份 `compose.deepagents.pre-switch.yaml` 至 `/data/agent-studio/docker-compose/compose.deepagents-174.yaml`，使用现行 `.env.production` 与 `compose.yaml`、`compose.harbor.yaml`、`compose.deepagents-174.yaml` 执行 `up -d --no-deps --no-build --wait --scale worker=3 api worker`，恢复原 perf 镜像。回滚前重新核对非终态 Run。
- 3501 Web：停止 `axis-web-governance-64896583`，启动保留的 `axis-web-card-6d28480c`。旧镜像未删。配置与数据库备份不入仓库；此次无迁移，不需还原数据库。
