# Claude Agent SDK 升级验证

日期：2026-10-20。分支：`auto/zcode-projects-174`，基线：`cae7441a`。

## 变更

- Python SDK：`0.2.152` → `0.2.168`；内置 Claude Code：`2.1.259` → `2.1.301`。
- 更新 `pyproject.toml` 下限与 `uv.lock`；保留既有清华镜像源，其他依赖版本和包条目不变。
- 在线、离线增量 API Dockerfile 的 SDK 默认版本同步更新；离线镜像仍要求基座预装目标 SDK。
- `sandbox.Dockerfile` 的 CLI 版本同步到 `2.1.301`，避免沙箱与 Worker 使用不同版本。
- 本地 `.venv` 已安装新版。运行时代码、项目界面、数据库结构不变。

官方来源：[SDK v0.2.168](https://github.com/anthropics/claude-agent-sdk-python/releases/tag/v0.2.168)、[PyPI](https://pypi.org/project/claude-agent-sdk/0.2.168/)。

## 验证

- `uv lock --check --default-index https://pypi.tuna.tsinghua.edu.cn/simple` 通过。
- `uv pip check --python .venv/bin/python`：123 个已安装包兼容。
- 直接运行 SDK 内置二进制 `--version`：`2.1.301 (Claude Code)`。
- 运行时、沙箱及 Claude fake-transport 集成测试：**550 passed, 4 skipped**（19.82 秒）。4 项跳过依赖未安装的可选 DeepAgents；同时显式排除 6 个 DeepAgents 专属模块。本次不是全项目 CI。

回归命令：

```sh
.venv/bin/pytest -q tests/unit/runtime tests/unit/sandbox \
  tests/integration/runtime/test_claude_runtime_fake_transport.py \
  --ignore=tests/unit/runtime/test_deepagents_runtime.py \
  --ignore=tests/unit/runtime/test_deepagents_backend.py \
  --ignore=tests/unit/runtime/test_deepagents_tool_gate.py \
  --ignore=tests/unit/runtime/test_deepagents_events.py \
  --ignore=tests/unit/runtime/test_deepagents_staged_assets.py \
  --ignore=tests/unit/runtime/test_registry_deepagents_runtime.py
```

- 真实 CLI 验证：`PYTHONPATH=src .venv/bin/python scripts/probe_warm_sdk.py --samples 3`，冷启动/恢复 3 轮、会话复用 3 轮，全部成功。6 次 MCP 发布和 6 次 Hook 的 Run 上下文均正确，每轮产物内容匹配当前 Run；复用组后两轮确实复用了连接。涵盖 context usage 与结束后 SessionStore flush。
- [真实 CLI 验证原始结果](results/claude-sdk-0.2.168-local-20261020.json)。模型响应来自 localhost 固定响应服务，无外部模型调用；不能据此推断线上模型质量或首字性能。
- 定向 Pyright（明确指定 `.venv/bin/python`）检查 `claude_sdk.py`、`warm_sdk.py`、`sdk_tool_gate.py`、`message_mapper.py`、`sandbox/claude_cli.py`。共有 13 条既有诊断，全部位于 `message_mapper.py:96–115` 的图片脱敏字典类型。临时换回 SDK `0.2.152` 后逐字对比输出完全一致，未新增类型诊断；随后已恢复 `0.2.168`。不将这次类型检查标记为全绿。
- `git diff --check` 通过。

## 发布范围

本次完成本地依赖及构建配置升级、验证，**尚未重建或部署 174 的 API/Worker/沙箱镜像**，线上仍使用原运行时。发布时需同时升级 API 与 Worker 的 SDK 依赖层，不能只复制源码；离线增量镜像需使用已装 `0.2.168` 的新基座。另需检查部署环境是否显式设置旧的 `HARNESS_*_CLAUDE_CLI_VERSION`，避免覆盖新版内置 CLI。线上真实模型与远程沙箱链路应在部署后验收。
