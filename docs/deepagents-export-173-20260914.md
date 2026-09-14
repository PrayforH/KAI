# DeepAgents 项目导出 · 173 环境部署与验证记录

> 本文记录 173 上 r1–r7 的历史实现与验证。后续合并前修复、实跑结果和能力边界见 [develop 导出审查](deepagents-export-develop-review-20260914.md)；本次审查未重新部署 173/174。

- 日期：2026-09-14
- 分支：`feature/weknora-knowledge-base`（工作区全量状态）
- 目标环境：`172.20.109.173`（API `:8800`，Web `:3301`）
- 发布 tag：`deepagents-export-20260914` → `-r2` → `-r3` → `-r4` → `-r5` → `-r6` → **当前 `-r7`**（web 停留在 r1 同功能版，后续修订均为 API 侧）
- 上一版：`kb-wiki-form-20260914`
- 回退点：`/data/agent-studio/docker-compose/.env.production.bak-deepagents-export-20260914-*`（含 `-r2-*`/`-r3-*`/`-r4-*`）

## 0. 修订历史（同日，来自导出项目实测反馈）

对真实导出的 `parenting-expert-0.1.2-deepagents` 做检查时发现 3 个生成器缺陷，全部修入导出器：

1. **生成的 `mcp.py` 遮蔽第三方 `mcp` SDK（阻断性）**：`langchain_mcp_adapters` 导入
   `mcp.ClientSession` 时命中本地模块，报 `cannot import name 'ClientSession' from partially
   initialized module 'mcp'`；`py-modules` 顶层注册使 `pip install -e .` 后全局覆盖真包。
   修复：改名 `mcp_servers.py`，同步 agent.py 导入与 pyproject。已在 0.7.13 venv
   复现旧命名报错并验证新命名导入正常。
2. **`.env` 不会被读取**：README 承诺 `cp .env.example .env` 但无 `load_dotenv`。
   修复：`agent.py` 在 `MODEL` 赋值前 `load_dotenv(Path(__file__).parent / ".env")`
   （已有环境变量优先，缺文件静默），`pyproject` 显式加 `python-dotenv>=1.0,<2.0`。
3. **终端 dump reasoning 块**：`main.py` 改用 `_answer_text()`，只输出 `type=="text"` 块。
4. 附带：生成 `.gitignore`（`.env`/`.venv/`/`__pycache__/`/`*.egg-info/`/`workspace/`/`.DS_Store`）。

r2 线上验证：建草稿 → 导出 200 → ZIP 契约（含 .gitignore、无 `mcp.py`、`load_dotenv`/
`_answer_text`/python-dotenv 断言）通过，冒烟草稿已删（204）。

### r3：macOS 快速开始命令

用户在本机执行 `pip --version` 报 `command not found`（python.org 安装只有 `python3`/`pip3`），
暴露 README 快速开始写的 `python -m venv` 在 macOS 默认安装上第一步即失败。
改为 `python3 -m venv`，并加注记：venv 激活后 `python`/`pip` 才存在；
`CERTIFICATE_VERIFY_FAILED` 时用 `SSL_CERT_FILE=/etc/ssl/cert.pem pip install -e .`。

### r4：provider 判定与托管凭据说明

用户追问「.env 中 4 项都要配吗」，暴露两个真问题：

1. **provider 判定错误**：原实现按模型名前缀猜 provider，`deepseek-v4-pro` 不含
   claude/gemini 前缀于是落到 `openai:`；但平台路由目录里该路由的 `apiFormat` 是
   `anthropic_compatible`（173 上四条生产路由全部如此）。平台路由模型名是**别名**，
   本身不携带协议信息。修复：以路由目录的 `apiFormat` 为准
   （`anthropic_compatible`→`anthropic`，`openai_compatible`→`openai`），
   仅无路由时才回退到名字启发。`service.deepagents_project()` 现按草稿 `routeId` 传入路由。
2. **托管凭据不导出这一事实未声明**：生产路由 `credentialManaged=true`，
   平台凭据不可能随导出提供，但原 `.env.example` 只是 4 行全注释，
   既没标必需/可选，也没说要自备端点。修复：`.env.example` 改为按 provider
   只呈现该 provider 的必需变量并标注可选覆盖项，显式写明「平台凭据由平台托管、
   不随导出提供」；README 增加 provider→必需变量对照表，
   `agent-studio.json` 增记 `deepagentsProvider` / `routeApiFormat` / `routeCredentialManaged`。

r3/r4 线上验证：导出 200，契约断言（`anthropic:deepseek-v4-pro`、仅呈现
`ANTHROPIC_API_KEY`、含「平台托管」与 `apiFormat` 说明、扩展字段）通过，冒烟草稿已删（204）。
单测 7 项 + studio 全量 185 项通过，ruff 干净。

> 注意 r4 改变了默认 MODEL 串（`openai:` → `anthropic:`）。已下载的旧项目不受影响；
> 若开发者用 DeepSeek 官方 OpenAI 兼容端点，设 `DEEPAGENTS_MODEL=openai:<model>` 覆盖即可。

### r5：checkpointer（修复审批死路）

对照算法组手写的生产项目（`agent-with-sandbox`，`create_deep_agent` + Daytona 沙箱 + 传
checkpointer）后核查发现：**我此前完全没传 checkpointer，而这不是「少个可选功能」，是个 bug**。

- 生成的 `main.py` 原本只传 `recursion_limit`；而 read-only + Bash 的草稿会发
  `interrupt_on={"execute": True}`。实测（0.7.13）：
  - 无 checkpointer 时 interrupt 照常抛出，但 `Command(resume=...)` 直接
    `RuntimeError: Cannot use Command(resume=...) without checkpointer`——
    生成的 CLI 会打印空答案退出，挂起的审批**永远无法恢复**。
  - `checkpointer=True` 对根图非法（`RuntimeError: checkpointer=True cannot be used
    for root graphs`），必须传实例。
  - 有 checkpointer 时 `config` 必须带 `thread_id`（否则 `ValueError: Checkpointer
    requires ... thread_id`）。

修复：生成 `CHECKPOINTER = InMemorySaver()`（`langgraph.checkpoint.memory`）并接入
`create_deep_agent`；`main.py` 传 `thread_id`（`DEEPAGENTS_THREAD_ID`，默认 `cli`），
并在有审批时生成 `_resolve_interrupts()` 交互循环——打印待批动作、询问 y/N、
`Command(resume={"decisions": [...]})` 恢复。

实测（真实 0.7.13）：
- 批准路径：`execute` 真实执行，工具返回 `approved\n\n[Command succeeded with exit code 0]`
- 拒绝路径：工具未执行，流程正常收敛
- 单测 7 项、studio 全量 240 项通过，ruff 干净

> 早期结论「不接 DeepAgents runtime 因为 checkpointer 会与平台分叉」不适用于导出：
> 导出产物从不在平台内运行，checkpointer 不存在「双重执行者」问题。
> r5 线上契约断言通过，冒烟草稿已删。

### r6：改为完整的 LangGraph/deepagents 工程（入口形状重做）

对照 `agent-with-sandbox` 与 deepagents 官方 README 后确认：**r1–r5 的
`main.py "任务"` 形式不是框架的用法**。官方 README 的做法是
`create_deep_agent(...)` 返回编译好的 LangGraph 图，由 LangGraph 工具链承载
（本地 `langgraph dev`/Studio，生产走 LangGraph Platform）。参照项目也正是
`langgraph.json` + 模块级入口。

读 `langgraph_api` 源码确认三条硬契约（不是猜的）：

1. **图工厂必须同步**：`_factory_utils.invoke_factory` 是 `return value(**kwargs)`，
   **不 await**，async 工厂不被支持；`classify_factory` 支持 0/1(config)/1(runtime)/2 参数，
   0 参即 `value()`。
2. **不能自带 checkpointer**：`graph.py` 在 `PERSISTENCE` 语境下，若图带
   `BaseCheckpointSaver` 会直接 `raise ValueError`（"With LangGraph API, persistence is
   handled automatically by the platform"）；平台会用自己的 checkpointer/store 覆盖。
   **这与 r5 的无条件 `InMemorySaver()` 正好相反**——r5 的修复对独立 CLI 是对的，
   对 LangGraph 工程是错的。现按平台语义移除，审批中断由平台侧挂起/恢复。
3. **MCP 的异步发现不能放在工厂里**（工厂同步），改用 `McpToolsMiddleware`：
   `awrap_model_call` 首次调用时拉取工具并 `request.override(tools=[...])`，
   `wrap_model_call` 显式报错（同步路径会静默丢工具，必须大声失败）。

结构变化：

| 之前 | 现在 |
| --- | --- |
| `main.py`（argparse + task） | **删除**；`langgraph.json` 为入口 |
| 自带 `InMemorySaver` | 不传（平台管理持久化） |
| `build_agent` 分同步/异步两支（MCP 走异步） | 统一同步 `build_agent()` + `agent()` 零参工厂 |
| `mcp_servers.load_mcp_tools()` 在构建时调用 | `McpToolsMiddleware` 首次调用时注入 |
| pyproject 无 langgraph | 加 `langgraph-cli[inmem]`；`py-modules` 去掉 `main` |
| `.gitignore` 无 langgraph 状态 | 加 `.langgraph_api/` |

实测（真实 0.7.13 venv）：
- 按 `langgraph.json` 解析：`agent` 是同步零参可调用 → 返回 `CompiledStateGraph`，
  `checkpointer is None`，节点齐全
- 工具面：`execute/glob/grep/ls/normalize_score/read_file/task/write_file/write_todos`
- MCP 分支：`McpToolsMiddleware` 在栈中、async 钩子已覆写、同步路径大声报错、
  缺失环境变量报 `Missing environment variable TAVILY_API_KEY`
- 单测 7 项、studio 全量 240 项通过，ruff 干净
- 173 线上契约断言通过（无 `main.py`、`langgraph.json` 正确、无自带 checkpointer、
  `langgraph-cli` 在依赖里、README 含 `langgraph dev`），冒烟草稿已删

#### r6 补充验证：`langgraph dev` 真实启动

r6 只验证了图能被解析，未跑过真实服务。补测（在用户新导出的
`parenting-expert-0.1.2-deepagents` 副本上）：

```
[info] Importing graph profiling  graph_id=parenting-expert  path=./agent.py  elapsed_seconds=1.47
[info] Application started up in 3.363s
POST /assistants/search -> {assistant_id: 00bcc817-…, graph_id: parenting-expert, name: parenting-expert}
```

即 `langgraph dev` 能按 `langgraph.json` 加载 `./agent.py:agent` 并对外提供该图。

### r7：IDE（PyCharm）接入说明

用户用 PyCharm 加载新导出的项目后无法启动。定位到原因：**PyCharm 新建的 venv
里既没有 pip、也没有安装任何包**（`.venv/lib/.../site-packages` 仅 3 项，
`_virtualenv.pth` 表明由 virtualenv 建出且未 seed pip），因此
`langgraph dev` 没有可执行文件、项目也未安装。

这暴露生成项目缺 IDE 接入指引。r7 补齐：

- README 新增「在 IDE（PyCharm / VS Code）里运行」：设置解释器 →
  `python -m ensurepip --upgrade`（PyCharm 的 venv 可能不带 pip）→
  `python -m pip install -e .`（`langgraph dev` 要求项目已装进该解释器，
  否则报 "you haven't installed your project and its dependencies yet"）→
  IDE 终端跑 `langgraph dev`；另给 `from agent import agent` 的调试写法。
- 提到 `uv venv && uv pip install -e .` 作为规避本机 CA 证书问题的省事路径。
- `.gitignore` 增补 `.idea/`、`.vscode/`。

已实测 `python -m ensurepip --upgrade` 可修复无 pip 的 venv（装上 pip 24.2）。
单测 7 项 + studio 全量 240 项通过；r7 线上契约断言通过。

## 1. 交付内容

新增第三条导出路径（与平台 Bundle、NexAU ZIP 并列）：把 Studio 草稿导出为**可直接运行的
DeepAgents Python 工程**，运行时钉死 `deepagents==0.7.13`（PyPI，2026-09-02，
requires-python `>=3.11,<4.0`）。

| 项 | 内容 |
| --- | --- |
| 后端 | `src/harness/studio/deepagents_export.py` 生成器；`service.deepagents_project()`；`GET /v1/studio/drafts/{id}/deepagents-project`（`X-Agent-Export-Format: deepagents`） |
| 前端 | 工作台「任务 · 导入与导出」菜单新增「导出 DeepAgents 项目」；`studio-client.downloadDeepagentsProject()` |
| 测试 | `tests/unit/studio/test_deepagents_export.py` 5 项；`test_agent_studio_api.py` 生命周期用例扩展 deepagents 断言；前端菜单契约 10→11 项 |

不涉及 runtime 注册表、能力契约、持久化模型（`alembic_version` 不变），worker 无需滚动。

## 2. 生成物结构与映射

```
<name>-<version>-deepagents.zip
├── pyproject.toml         # deepagents==0.7.13 + langchain-openai（+mcp adapters 按需）
├── README.md              # 快速开始 + 未导出语义清单
├── .env.example           # 模型与 MCP 凭据占位
├── agent-studio.json      # 溯源 + droppedSemantics 丢弃声明
├── agent.py               # create_deep_agent 装配（build_agent 可注入测试模型）
├── main.py                # CLI：python main.py "任务"
├── mcp.py                 # 仅当选择 MCP：langchain-mcp-adapters 0.3.x 连接
├── tools/<name>.py        # 平台 Python 工具 → StructuredTool（inputSchema 即暴露 schema）
├── skills/<name>/         # SKILL.md + 附加文件
└── subagents/<alias>.py   # SubAgent dict（isolated，单层）
```

关键映射（全部在真实 0.7.13 安装上验证过，不是照抄文档）：

- **默认栈没有 `TodoListMiddleware`**：`write_todos` 需显式 `middleware=[TodoListMiddleware()]`。
- **自定义 `FilesystemMiddleware(backend=…, tools=…)` 会整体替换内建栈**：以此裁掉 `delete`、
  仅在选择 Bash 时加入 `execute`；`ls`/`read_file` 作为技能渐进披露依赖默认追加，
  在 `addedExportTools` 声明。
- **`FilesystemPermission` 与可执行 backend 在 0.7.13 互斥**（`NotImplementedError`）：
  - 有 Bash → `LocalShellBackend` + `execute`，放弃 workspace permissions；若策略为
    `production-read-only` 改发 `interrupt_on={"execute": True}`（进程内审批近似）。
  - 无 Bash + `production-read-only` → `FilesystemBackend` + `permissions` 真实拦截写入。
- skills 物化：`SkillsMiddleware` 相对 backend 根读取，生成代码启动时把 `skills/` 镜像进
  `workspace/`（对应平台 `.claude/skills` 语义）；`skillReferences` 经编译器解析后一并携带。
- MCP：凭据从不导出，`$__ENV__NAME__` 占位符启动时解析，缺失即报错。

## 3. 验证结论

| 层 | 验证项 | 结果 |
| --- | --- | --- |
| 单元 | `tests/unit/studio/test_deepagents_export.py` | 5/5 通过 |
| 集成 | `test_agent_studio_api.py`（含新路由断言） | 55/55 通过 |
| 契约 | ruff；`tsc --noEmit`；vitest 587 过 / 3 失败均为改动前既有问题 | 通过 |
| 实跑（本地 venv，真实 0.7.13） | 无 Bash 分支：编译 + `edit_file/ls/read_file/write_file/write_todos/task/normalize_score`，skill 注入 system prompt，工具实调 `{'n': 0.42}`，`delete` 不存在 | 通过 |
| 实跑（本地 venv） | Bash 分支：`execute/glob/grep` 出现、`delete` 不存在 | 通过 |
| 线上 173 | 建草稿 → `deepagents-project` 200，`filename="deepagents-smoke-0.1.0-deepagents.zip"`，ZIP 契约（钉版/中间件/丢弃声明/全部 .py 可编译） | 通过 |
| 线上导出实跑 | ZIP 拉回本地 0.7.13 环境：编译 + ainvoke 成功，工具面 `execute/glob/grep/ls/read_file/task/write_file/write_todos` 精确 | 通过 |
| 回归 | nexau-bundle 200；`/healthz`、web 200；知识库/技能页标记串在镜像内命中 | 通过 |

镜像标记检查（事故教训：web 必须全量工作区构建）：

```
HIT: 分块设置 / Wiki 设置 / wikiContentInstructions（在途知识库工作未被回退）
HIT: 导出 DeepAgents 项目 / deepagents-project（本次新功能）
```

## 4. 部署步骤（可复现）

```bash
TAG=deepagents-export-20260914

# 1) API：code-only 增量镜像，在 173 上构建（构建上下文 = 工作区 src/harness）
#    BASE_IMAGE 取当时在跑的 api 镜像
tar --exclude='__pycache__' -czf harness.tgz src/harness
scp harness.tgz deploy/docker/api-code-only.Dockerfile 173:/data/agent-studio-builds/$TAG/
ssh 173 "cd /data/agent-studio-builds/$TAG && tar -xzf harness.tgz && \
  docker build --build-arg BASE_IMAGE=harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:kb-wiki-form-20260914 \
  -f api-code-only.Dockerfile -t harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:$TAG . && \
  docker push harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:$TAG"

# 2) Web：本地 buildx 交叉构建 amd64，**从工作区全量构建**（见 20260914 事故记录）
docker buildx build --builder agent-deploy-http --platform linux/amd64 \
  --build-arg NODE_IMAGE=docker.m.daocloud.io/library/node:22-alpine \
  -f deploy/docker/web.Dockerfile \
  -t harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:$TAG --load .
docker push harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:$TAG

# 3) 切换（备份 .env.production → 改 HARNESS_HARBOR_IMAGE_TAG → 三文件 compose 重建 api/web）
#    注意 --pull never：compose.harbor.yaml 的 pull_policy: always 会触发对 docker.io 的
#    拉取并在无外网的 173 上超时；所需镜像先手动 pull 后用 --pull never --no-deps api web
```

## 5. 边界与后续

- 导出是**生成器不是 runtime**：知识库、平台记忆、持久化审批、Bash 策略门、评测、配额
  不可导出，全部在 `agent-studio.json` 的 `droppedSemantics` 与 README 中显式声明。
  草稿带知识库引用时导出仍成功，但声明「导出项目不能检索知识库」。
- `deepagents` 版本面变化快（`state_schema`/`HarnessProfile`/`RubricMiddleware` 都是
  0.7.x 新增），升级钉版前需重跑 `test_deepagents_export.py` + 0.7.13 venv 实跑冒烟。
- 173 宿主机与 api 容器内各留了一个 `/tmp/da_*.zip|py` 校验残留（root 属主，重启即清）。
- worker/quality-sync 仍为 `route-guard-20260910-r2`：本次改动不触及持久化与执行路径，
  无需同批滚动。
