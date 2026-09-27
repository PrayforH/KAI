# 多分支整合 develop 与 174 发布验证（2026-09-27）

## 合并范围

从干净 `origin/develop=6d28480c` 新建集成分支，按顺序合入治理/性能线 `d9633f7b`、动效/技能/过程线 `88314059`、观测控制台 `c744d02c`；性能线额外 `512ad938` 仅补文档。合并提交分别为 `4e88fbea`、`28fc8847`、`72cca789`，最终类型收敛提交 `3ab89d5f735bf55dce56785641272b6044640f0b`。本地与 `origin/develop` 均快进到此提交，无强推。

治理线已包含性能线截至 `63961e74` 的代码，不重复合并；主工作区仍是旧 detached 脏树，未提取其旧代码或清理未跟踪材料；分叉 QA 线未整线合并。

## 合并验证

- Git 无文本冲突；人工对账 `agent-thread.tsx` 的持久回答/单份过程、`durable-history-sync.tsx` 的历史交接，以及观测与动效共享 CSS；保留 Trace 入口、思考/运行中动作扫光和 reduced-motion 规则。
- 后端完整 unit+integration：1854 passed，8 skipped；6 条既有弃用提示。
- Web Vitest：148 文件，957 passed，1 skipped；Next 16.3.3 生产构建和 TypeScript 通过。
- 专项：81 个后端授权/产物/活动/发布测试通过，7 个前端文件的 59 项回答交接/过程/Trace/动效/搜索测试通过。
- 本次变更文件 Ruff 全通过；全仓 Ruff 有 3 条旧测试风格问题（`tests/unit/automations/test_service.py` 两处、`tests/unit/projects/test_service.py` 一处）。关键变更模块 Pyright 为 0 错；观测线 `activity.py` 的 3 个局部未知类型已在最终提交中无行为变化地收敛。
- 无新增迁移、Agent 或 platform-skills 文件。

## 174 发布

- 发布前 API 为 `kai/axis-api:governance-artifact-a4d73290`，三 Worker 为 `kai/axis-api:governance-64896583`，3501 独立 Web 为 `kai/axis-web:governance-64896583`；无非终态 Run，数据库 `0037`。
- 备份 `/data/releases/develop-3ab89d5f/`（mode 700）：原 Compose SHA256 `e02b4682592ba1971501c2486e2c89fc2c1992152635f4ecde056dc1c0e98967`、env、容器 inspect、PostgreSQL custom dump 103,443,203 字节。备份文件不入仓库。
- 在 174 linux/amd64 上以前述现行镜像为基座，从同一提交的干净源码/Next standalone 构建 API/Web 叠加镜像；revision label 均为 `3ab89d5f735bf55dce56785641272b6044640f0b`。
  - API/三 Worker：`kai/axis-api:develop-3ab89d5f`，image ID `sha256:ab36989ea42e332b22c5b6befa6b859971eae281765df803e3c1e49ddb62101d`。
  - 3501 Web：`kai/axis-web:develop-3ab89d5f`，image ID `sha256:726f37fa89fe7cff543309e7e89eaf13fdaf3b9ed9a1b64e8f89f7bfd66d96c2`，Build ID `CS3VvTpf4mBBNux3MKRZj`。
- 3599 Web canary `/`、`/login` 200；API import smoke 通过，镜像 `alembic heads=0037`，无需迁移。切换后 API/三 Worker 全 healthy，3501 `/`、`/login`、8800 `/healthz` 均 200，数据库仍 `0037`。旧 `axis-web-governance-64896583` 停止保留，3599 临时容器已删除。174:3301 原 Web 仍 200；173 未操作。
- 线上容器验证 `production-orchestrator` 对 `Skill` allow、未知工具 deny、standard 对 Task deny；`pptx-generator` 目录条目存在。3501 静态 chunk 含 `run-trace-console` 和动效规则。
- 真实产物 API：所属用户任务目录返回 200、3 个文件，包含 `Agent 自进化技术地图 · 中英双语 PPT（29 页）.pptx`；下载 200、105,607 字节；其他用户/租户及错误任务绑定均 404。

## 未执行与回滚

没有新建真实模型任务或在线上发起生产晋升；登录后的 Trace/动效/过程视觉验收受单设备会话限制未做。服务健康和代码/接口证据不能代替该视觉验收。

回滚前先检查活跃 Run：恢复 `/data/releases/develop-3ab89d5f/compose.pre-switch.yaml` 为 `/data/agent-studio/docker-compose/compose.deepagents-174.yaml`，用现有 `compose.yaml`、`compose.harbor.yaml`、`.env.production` 仅重建 API/三 Worker；停止 `axis-web-develop-3ab89d5f`，启动保留的 `axis-web-governance-64896583`。无需数据库降级。旧镜像和备份均保留。
