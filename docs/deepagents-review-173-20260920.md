# DeepAgents 分支复审与 173 验证

日期：2026-09-20。

复审结论：本轮发现的阻塞问题已修复，相关回归与 173 实机验证通过，允许合入 develop。

## 修复范围

- Bundle Python 工具仅可覆盖隐式拒绝，显式 DENY 保持生效，拒绝时不会调用工具处理器。
- 动态发现的 MCP 工具在执行阶段绑定具体工具实例，并继续经过平台工具权限检查；发现结果限定为发布目录内的工具。
- Sandbox 后端不再接收 DeepAgents 0.7.13 不支持的 `_permissions` 参数；只读限制由 Run 已解析的平台策略执行。真实图测试同时检查可读、不可写和标准策略可写。
- 每次 model 节点响应均累计 token 和轮次，包含工具调用轮次与最终回答，供 runtime.result、配额和观测共用。
- 实机验证补充发现并修复虚拟绝对路径误拒绝：工具权限检查与文件后端共用路径解析，`outputs/x`、`/outputs/x`、`/workspace/outputs/x` 指向同一工作区文件，`..` 仍拒绝。
- CI 显式安装 DeepAgents optional extra 并运行该运行时的单元、注册器及真实图集成测试，避免默认环境跳过核心回归。

## 版本与环境

- 原分支：`feature/deepagents-runtime`，`4a43a40`。
- develop 基线：`7cbd6ae`。
- 第一轮修复：`e7d4cde`；待验证合并：`9794612`；路径修复与 CI：`91f5e16`。
- 173：`172.20.109.173`，以现有 `kai/axis-api:deepagents-20260919.9` 启动独立验证容器，挂载合并后的源码。
- DeepAgents 0.7.13 / LangChain 1.4.1 / langchain-core 1.6.3 / MCP adapters 0.3.2。
- 本地与 173 的 311 个 Python 源文件聚合 SHA-256 一致：`5f525041f58d348c01004eff3199808203c1126c426440bf4d0fad0cf318cc7a`。
- 验证没有替换 173 正在服务的 API、worker、web 容器。线上 API 与三个本机 worker 检查均为 healthy。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 本地：runtime / studio / observability / composition / contract / Studio API 及图集成回归 | 630 passed，30.43s |
| 173：同一组回归 | 630 passed，93.96s |
| 最后补充的实际文件写入图测试，与读取/拒绝/MCP 图测试一起执行 | 本地与 173 各 4 passed；覆盖了 630 项之后增加的一个可写场景 |
| 控制台 agent-studio 与 runtime-capabilities-contract | 22 passed |
| CI 新增的 DeepAgents optional-extra 回归命令 | 82 passed |
| 本次修复涉及的 Python 文件 Ruff、git diff --check | 通过 |
| 新增图集成测试 Pyright | 0 errors |

后端回归命令：

```sh
python -m pytest -q \
  tests/unit/runtime tests/unit/studio tests/unit/observability \
  tests/unit/api/test_runtime_composition.py \
  tests/contract/test_runtime_capabilities_contract.py \
  tests/integration/api/test_agent_studio_api.py \
  tests/integration/runtime/test_deepagents_graph.py
```

### 173 真实模型验证

使用 173 的 tenant `local` 已配置路由 `deepseek-v4-flash`，实际模型 `deepseek-flash`，协议 `anthropic_compatible`。凭据仅在测试进程中解析，不写入报告或脚本。事件与审批状态使用独立内存仓库，文件命令在临时验证容器的临时工作区执行。

| 场景 | 断言 | 结果 |
| --- | --- | --- |
| 只读 | 模型真实调用 read_file 读取预置验证标记，正常结束，未生成输出目录 | PASS |
| HTTP MCP | 启动临时 FastMCP HTTP 服务，真实发现并执行 lookup，服务端收到 query=review，返回标记到模型 | PASS |
| 文件写入 | 模型调用 write_file 后读回；检查磁盘 outputs/review.txt 内容准确 | PASS |

三次最终成功 Run 的轮次分别为 3、2、6；输入/输出 token 分别为 8479/602、5278/87、21340/2055。写入场景中模型曾提前读尚不存在的文件，随后成功写入并读回；验证以最终磁盘内容和成功工具调用为依据，允许模型处理可恢复的文件未找到错误。

实机证据保留于 `/data/agent-studio/builds/deepagents-review-9794612/`：`pytest-final.log`、`graph-final.log`、`live.log`、`live-smoke.py` 和验证源码。

## 环境与基线说明

173 旧基础镜像缺少 libarchive 及其系统依赖，首轮 9 项失败由该缺依赖和测试包漏带 security fixture 引起。在独立测试容器补装 libarchive13/libxml2/libicu72，并补齐仓库 fixture 后，完整回归全部通过。仓库 Dockerfile 已包含 libarchive 安装步骤；本次没有修改线上容器。

整仓静态检查仍有历史欠账，不记为全绿：`ruff check src tests` 有 4 处既有 E501，位于未改动的 `test_approval_flow.py`、`test_model_configuration.py`；全量严格 Pyright 当前为 750 个诊断，在同一解释器下对未修复合并树的检查为 831 个。新增集成测试单独类型检查为零；本轮未扩大到整仓类型治理。
