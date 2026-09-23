# DeepAgents 导出工程化增强（174，2026-09-23）

## 变更

参考 `zhfkt/sapling-deep-agents` develop `a877352e` 的分层组织方式，将 Agent Studio 导出包改为可安装的 `src/sapling_deep_agents` 工程。功能分支 `feature/deepagents-project-layout`，实现提交 `2e169a88`、`499f0368`，合并提交 `7cd47358` 已推送到 develop。

- `agents`：主图装配、固定版本子智能体；保留同步工厂和原 DeepAgents 0.7.13 语义。
- `config`、`prompts`：环境配置与可编辑系统提示词分离；根 `.env` 在子智能体加载前生效，保留已有子智能体模型覆盖变量名。
- `controller`、`run`：公共图入口与使用当前 Python 解释器的服务启动器；根 `agent.py` / `langgraph.json` 兼容入口保留。
- `middleware`、`services`、`skills`、`tools`：MCP、委派控制、资源读取、技能附件与原始算子分离；`models` / `utils` 提供扩展位置。
- wheel/sdist 打包提示词、技能文件（包括隐藏目录与二进制附件）及子智能体。workspace 默认在项目工作目录，可用 `DEEPAGENTS_WORKSPACE` 覆盖，不向 site-packages 写状态。技能复制对相同内容不重复写入，也不删除额外用户文件。
- 新增 Dockerfile、Compose、本地工程测试、GitLab CI、pre-commit、Pyright 配置、AGENTS.md 和工程说明。
- 元数据记录 `projectLayout=src-v1`；导出器保持离线、可重复生成。`uv.lock` 由使用者运行 `uv lock` 按自己的包索引生成，不伪造锁文件或 Copier 来源。

参考仓库仅用于结构参考，没有复制其业务接口、凭据或沙箱配置。原有知识库、记忆、平台审批等迁移边界继续在生成 README/元数据中声明。Docker 模板运行 LangGraph 开发服务；生产持久化和认证需使用 LangGraph Platform 方案。

## 验证

- Studio 单元测试、导出/预览/权限/固定发布子智能体 API、builder diff，以及真实 DeepAgents 执行测试：281 passed。
- 启动器最后调整后，5 项真实运行与 wheel 回归再次通过。
- Ruff、改动模块 Pyright（0 errors）、git diff 检查通过。
- 独立 Python 3.12 环境仅安装导出工程声明的依赖与构建工具；editable 安装、wheel 安装、`uv pip check` 通过。
- 验证真实框架的 MCP 工具发现与调用、Python Schema、只读写入限制、Bash 审批恢复、子智能体委派、根 `.env` 与子智能体模型、wheel 脱离源码加载提示词/二进制/隐藏资源、重复技能物化不修改文件时间。
- 导出工程自带的 pytest（2 项）及 Ruff 通过；wheel 与 sdist 构建成功。Docker 模板在本地实际构建成功；额外的容器启动检查因本机 Docker 磁盘空间不足（ENOSPC）未完成，未将其计为通过。原生 LangGraph 启动及线上 API 验收已通过。
- 174 线上下载的 ZIP 再次构建 wheel/sdist，安装 wheel 后运行自带 pytest（2 项）、Ruff 和 `uv pip check` 均通过。
- `python -m sapling_deep_agents.run.app` 实际启动 LangGraph，`/ok`、创建 assistant、读取 `/assistants/{id}/schemas` 均成功。模型使用占位凭据，框架执行测试使用测试模型，没有调用外部付费模型。

## 174 发布

运行 API：`kai/axis-api:deepagents-project-layout-499f0368`，镜像 ID `sha256:9a88f74871b22a0df98933e4326d8387bca8cd73988ef8623d77af572ed68205`。

增量基于线上 `wt-151e4a02`（`sha256:c8a6d64dc88d8d531607bd47da0e4a655cc662102bea39d86c7b28c600b56e28`），仅覆盖 `harness/studio/deepagents_export.py` 与新增 `deepagents_scaffold.py`。发布前比对线上导出器与 develop 基线 SHA-256 一致；API 重建前后核对原镜像未被并发更新。Worker、3501/3301 Web 与数据库未变更。

- `/healthz` 200；3501 页面 200；API healthy。
- 通过真实 HTTP 建立临时草稿并下载 ZIP：200 / application/zip，37 个文件，全部 Python 文件可解析，无 ZIP 重复项。
- 代码预览逐文件 digest 与 ZIP 一致；跨用户 404；过期 revision 409；临时草稿删除 204。
- 线上样本 SHA-256：`6fd5cabb08a3250a404d157d3d922f09411e4db590d88da350b13dd70d95a727`。
- 发布脚本、源码包、原配置备份、验收脚本及 ZIP 样本保存在 174 `/data/deepagents-project-layout-20260923/`（仅 root 可访问）。

## 回退

将上述目录的 `compose.before.yaml` 恢复到 `/data/agent-studio/docker-compose/compose.deepagents-174.yaml`，再执行：

```sh
cd /data/agent-studio/docker-compose
docker compose --env-file .env.production -p agent-studio-174 \
  -f compose.yaml -f compose.harbor.yaml -f compose.deepagents-174.yaml \
  up -d --no-deps --no-build --pull never --wait api
```

本次没有数据库迁移。历史 ZIP 不受影响；重新导出即可获得新工程布局。
