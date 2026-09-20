# 174 DeepAgents 会话运行时修复

日期：2026-09-20。目标环境：172.20.109.174。

复审结论：本次修复通过相关回归和原会话实机验证，可以合入 develop。

## 根因与修复

截图会话 `session_7b017e2835144f0f95b148db741bcfe2` 固定使用 DeepAgents，
但 174 的 API 和三个 worker 均配置为 `HARNESS_RUNTIME=claude-sdk`。
旧配置将任务交给 Claude，随后尝试把 Claude 线程绑定到 DeepAgents 会话，产生
`is pinned to runtime deepagents` 错误。已部署的 `dea7358` 镜像包含上一轮 DeepAgents
代码修复，本次故障来自运行时部署配置。

直接启用原有 multi 配置还会启用 Codex。174 主机的 `user.max_user_namespaces=0`，
不满足 Codex bubblewrap 的启动要求；首次切换失败后已回滚恢复服务，再进行修复。

- 增加 `HARNESS_RUNTIME_KERNELS`，使用非空 JSON 数组选择 multi 模式的执行内核。
- 默认仍注册三个运行时；显式选择的内核必须完整接入路由器，未启用的内核明确拒绝执行，不能回退到其他运行时。
- 两个组合根仅加载选中的可选内核，关闭 DeepAgents 时不加载其可选依赖。
- worker 根据同一份经过验证的配置判断是否需要 Codex 沙箱校验；启用 Codex 时仍保留原校验，配置格式错误直接阻止启动。
- 增加 `compose.deepagents-runtime.yaml`，提供 Claude + DeepAgents 的部署配置。

174 当前使用 `HARNESS_RUNTIME=multi` 和
`HARNESS_RUNTIME_KERNELS=["claude-agent-sdk","deepagents"]`。
主机用户命名空间配置保持 0，容器保持默认 seccomp、`privileged=false`。
此环境不启用 Codex；已有 Claude 与 DeepAgents 会话由各自内核执行。

## 验证

| 检查 | 结果 |
| --- | --- |
| 本地 runtime / studio / observability / composition / config / deploy / contract / Studio API / DeepAgents 图集成 | 688 passed，54.31s |
| 174 同一组测试 | 首轮 684 passed，4 项因验证目录漏带 security、web 配置和 .dockerignore 文件失败；补齐后相关两个测试文件 14 passed，全部原失败项通过 |
| 生产组合根 | RegistryRuntimeRouter，内核精确为 Claude + DeepAgents |
| 原会话通过真实 API 入队、实际 worker 和模型执行 | succeeded |
| API、worker-1、worker-2、worker-3 | 均 healthy，全部使用同一修复镜像与运行时配置 |
| 修改的 Python 文件 Ruff、shell 语法、git diff --check | 通过 |

静态类型检查不作为整仓全绿声明。配置和路由器单独检查通过；组合根仍存在此前的类型诊断。

实机 Run：`run_687f94c7eaca47cdb34647151ec481fe`，输入为截图中的“你好”。
`model.route.selected` 明确记录 `runtime=deepagents`，路由 `deepseek-v4-flash`，
模型 `deepseek-flash`。`runtime.result` 为 success，1 轮，2920 ms，
input/output tokens 为 5013/337，产生完整助手回答和 `run.succeeded`。
原会话保持 `runtime_type=deepagents`，`runtime_thread_id` 为空，没有改写会话绑定。

测试命令：

```sh
python -m pytest -q \
  tests/unit/runtime tests/unit/studio tests/unit/observability \
  tests/unit/api/test_runtime_composition.py tests/unit/test_production_composition.py \
  tests/unit/test_config.py tests/unit/deploy/test_docker_assets.py \
  tests/contract/test_runtime_capabilities_contract.py \
  tests/integration/api/test_agent_studio_api.py \
  tests/integration/runtime/test_deepagents_graph.py
```

## 部署与回滚

- 执行代码提交：`4f95e64b`。
- 已推送 Harbor 镜像：`harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:deepagents-174-4f95e64b`。
- 镜像 digest：`sha256:e4745e6b5f58797c47532f067f6d7edce62aaa0828a8651dcc8c9e8ce86ecffc`。
- 镜像基于原部署的固定 digest 构建，仅覆盖已提交的 Python 源码和 worker entrypoint；基线到当前提交无依赖或数据库迁移差异。
- 本地源码与镜像内 317 个 Python 文件的聚合 SHA-256 一致：`694e63ebbc9785ec1aa8c792452ae1a704b9920daa59ba5d226ec32e7a1cc246`。
- 174 配置目录：`/data/agent-studio/docker-compose`。
- 环境专用覆盖文件：`compose.deepagents-174.yaml`，固定 API/worker 镜像和运行时。
- `.env.production` 的 `COMPOSE_FILE` 已包含该覆盖文件；重启入口为 `up-deepagents-174.sh`。
- 部署前活动任务数为 0，仅替换此项目的 API 与三个 worker。
- 备份：`/data/agent-studio/backups/deepagents-runtime-174-20260920`，其中环境文件和完整配置含凭据，不应复制到日志或仓库。
- 证据：`/data/agent-studio/builds/deepagents-runtime-174-4f95e64b/`，包含测试日志、构建文件、实机脚本和 `live-evidence.txt`。

需要回滚时，恢复备份中的 `compose.yaml` 与 `env.production`，使用原来的两个
Compose 文件重建 API/worker。原镜像仍保留。回滚会恢复 Claude-only 配置，
DeepAgents 会话也会重新受到本次故障影响。
