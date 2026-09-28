# 导出项目包名与 Builder 页头修复（2026-09-28，174）

## 变更

分支 `fix/deepagents-package-name-174`，两个提交，快进并入 `develop`（`c36ff62b` → `953d8cc2b1cf29c20eb46914ee3f194ee053747c`）：

- `b58aefd6` `fix(deepagents): name the exported project package deep_agents` —— 生成项目此前把内部代号当成 Python 包名：代码视图显示 `src/sapling_deep_agents/...`，文件正文 `from sapling_deep_agents.*`，下载 zip 的 `pyproject.toml`／`MANIFEST.in`／`langgraph.json` 也带着它。现在 `deepagents_scaffold.PACKAGE` / `PACKAGE_ROOT` 是唯一书写处，路径、import、setuptools include、MANIFEST graft、langgraph `pythonPackage` 与 README 正文都由它派生；控制台默认选中文件常量同步。
- `953d8cc2` `fix(builder): drop the identity icon left of the agent name` —— 名称左侧 30px 图标块及其两条 CSS 规则移除；按用户选择保留「● 草稿/已发布」状态、版本历史入口与保存状态。

本轮按用户确认执行**统一改名**（非显示层替换），所以代码正文与下载产物里的 import 也一致。

## 验证

- 新增两条后端守卫（`tests/unit/studio/test_deepagents_export.py`）：导出 zip 的文件名与全部文本内容不得出现 `sapling`；每个 `deep_agents.*` import 必须能在包内解析到对应模块，且 pyproject/MANIFEST/agent-studio.json 指向同一包。
- 后端：`tests/unit` 1542 passed；`tests/unit/studio` + `tests/integration/studio` + `tests/integration/api/test_builder_conversation.py` 250 passed/6 skipped；`tests/integration/api/test_agent_studio_api.py` 63 passed。
- 真实运行时契约（`tests/integration/studio/test_deepagents_export_runtime.py`，需要独立 DeepAgents venv）：本轮临时构建 `/tmp/deepagents-runtime-verify`（deepagents 0.7.13 + langchain-mcp-adapters + build + setuptools），`DEEPAGENTS_TEST_PYTHON` 指向它后 **5 passed**，含从生成项目构建 wheel（验证 `include = ["deep_agents*"]`）。
- 前端：Vitest 148 文件 / 957 passed / 1 skipped；`npm run build` 与 TypeScript 通过；`git diff --check` 干净。
- 静态检查：变更文件 Ruff 全通过；Pyright 全仓 1422 错，`953d8cc2` 与改前 `c36ff62b` 完全一致（基线 284 是旧环境漂移，非本次引入），变更文件 0 错。

## 174 发布

- 发布前：API/三 Worker `kai/axis-api:develop-3ab89d5f`，3501 独立 Web `axis-web-develop-3ab89d5f` / `kai/axis-web:develop-3ab89d5f`；`runs` 无非终态记录（succeeded 866 / cancelled 172 / failed 63 / rejected 7 / timed_out 5）；数据库 `0037`。
- 备份 `/data/releases/deepagents-pkg-953d8cc2/`（mode 700）：原 Compose SHA256 `c7698380781df1ede8bfd8fabd6946fd2e6167f59bae76052739d9d5acf12674`、`.env.production`、五个容器 inspect、PostgreSQL custom dump 104,110,970 字节。备份文件不入仓库。
- 在 174 linux/amd64 上以现行镜像为基座、从同一提交构建叠加镜像，revision label 均为 `953d8cc2b1cf29c20eb46914ee3f194ee053747c`：
  - API/三 Worker：`kai/axis-api:develop-953d8cc2`，image ID `sha256:e3d8bac866765f2a75f7cdb350c921fa13c056340ea79771329e096ed8fe1dba`。
  - 3501 Web：`kai/axis-web:develop-953d8cc2`，image ID `sha256:dbb27fc6e7219d5970f8e325f295250c29c79f698e8b799438be438cea9b2ca7`，Build ID `JPzVedh0kmIj3anhRmUJx`。
- 镜像内自检：`PACKAGE=deep_agents`，部署包内 `grep -rl sapling` 命中 0；`alembic heads = 0037`；web 镜像 44 个顶层 node_modules、含 `src/deep_agents/agents/agent.py` 的分片 1 个、`agentIdentityIcon` 分片 0 个。
- 3599 canary：`/`、`/icon.svg`、`/api/auth/config`、`/api/harness/runtime-config` = 200，`/api/auth/session` = 401，`/studio/spaces` = 404，容器 running/healthy、restarts 0、日志无启动错误；canary 已删除，3599 空闲。
- 切换：compose 内 API/Worker 镜像改为 `kai/axis-api:develop-953d8cc2` 后 `up -d --no-deps --scale worker=3 api worker`；3501 停旧 `axis-web-develop-3ab89d5f` 并改名 `axis-web-develop-3ab89d5f-rollback-953d8cc2` 保留，新容器 `axis-web-develop-953d8cc2` 继承原 env。切换后四容器 all healthy、restarts 0、日志无错误，`runs` 状态分布不变（无因重建产生的新失败），数据库仍 `0037`。
- 线上接口（真实草稿 `similar-case-analysis-agent`，41 个文件）：入口 `src/deep_agents/agents/agent.py`，`src/deep_agents/` 下 25 个文件，**含 sapling 的路径 0 个**。
- 线上静态分片：3501 提供的 `_next/static/chunks/0bcfyp-ygt1m_.js` 与本地构建字节一致（SHA256 `3d648509baa097104c4e32c505f10053629253b5253a551fdd51e0e1bcd237d7`），含新路径、不含 `agentIdentityIcon`。
- 浏览器验收（3501，测试账号，未使用真实用户账户以免挤掉其会话）：Builder 页头显示「合并版本代码视图验证 ● 草稿 v0.1.0 · 已保存 r1」，名称左侧已无图标且「● 草稿」保留；代码视图文件树显示 `src / deep_agents`，文件头 `src/deep_agents/agents/agent.py`，正文 `from deep_agents.config.settings import ...` 等 import 全部为新包名。
- 174:3301 原 Web 仍 200；173 未操作。

## 未执行与回滚

未新建真实模型任务、未做生产晋升（本轮改动与模型执行无关，且未经授权）。工具链内的 `ruff format` 与全仓 Pyright 基线在改动前就不干净，未顺手重排无关代码。

回滚：API/Worker 用 `/data/releases/deepagents-pkg-953d8cc2/compose.pre-switch.yaml` 恢复镜像标签并 `up -d --no-deps --scale worker=3 api worker`；3501 停 `axis-web-develop-953d8cc2`，把 `axis-web-develop-3ab89d5f-rollback-953d8cc2` 改回 `axis-web-develop-3ab89d5f` 并启动。无需数据库降级，旧镜像与备份目录均保留。
