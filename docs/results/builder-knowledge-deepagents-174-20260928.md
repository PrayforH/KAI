# Builder 勾选知识库报「不支持」的排查与修复（2026-09-28，174）

## 现象与根因

用户在 Builder「文件与知识」里勾选知识库后，保存/发布被「DeepAgents：当前运行时尚未接通 Studio Knowledge，请移除后发布」阻断。链路：

1. 用户的草稿 `similar-case-analysis-agent`（r11）是 **deepagents 运行时**，`knowledgeReferences: ["aipolicy"]`。本地复现：`AgentDraftCompiler.validate` 产出 ERROR `runtime_knowledge_unsupported`，`ready: False`。
2. 该错误来自能力目录：`claude-agent-sdk` 声明 `knowledge` 能力，`deepagents` 与 `codex-app-server` 声明「Knowledge references are not connected」；编译器对「勾选了目录未声明能力」的草稿按 ERROR 拒绝发布。而 Builder 的知识库选择器（`TaskKnowledgeProvider` + `KnowledgeBasePicker`）不看运行时，无条件可勾。
3. 深挖发现**第二个缺口**：即便走 claude-agent-sdk（运行时确实实现了知识检索，in-process MCP `harness-knowledge`），静态策略四个 profile 对 `mcp__harness-knowledge__*` 也全是 implicit-deny——`knowledge_search_read_rules()` 只放行了 `novel-search`/`knowledge-search` 两个外部 MCP 的工具，平台自己的知识工具一条规则都没有；174 库中也无治理发布包含它。即 Claude 路径勾了知识库，运行时第一次调用就会被策略拒。

## 修复（`7bdc9d81`，已快进推送 develop）

- **知识选择规则收敛到一处**（`harness/knowledge/runtime.py`）：`knowledge_bindings_for_run`（run 级 override 优先于会话 pinned bindings）、`knowledge_mode_for_run`（rag/wiki）、`knowledge_query_tool_name`（`mcp__harness-knowledge__query_knowledge_sources` / `search_wiki_pages`）、`knowledge_result_trust`（绑定含 untrusted 则 UNTRUSTED，否则 SENSITIVE）、RAG/WIKI 应答契约文案。`claude_sdk.py` 删除本地副本改用共享实现。
- **DeepAgents 运行时接通知识检索**（`deepagents_runtime.py` + `registry_deepagents_runtime.py` + `deepagents_factory.py` + 两处 compose 注入点）：wrapper 从会话/run 输入解析绑定，无服务时拒绝（与 Claude 路径同语）；`_knowledge_tools` 以运行身份直连 `KnowledgeService.search` / `search_bound_wiki_pages`，工具名与 Claude 路径完全一致，策略/配额/门限用同一个词表；系统提示词追加同一份应答契约；`DeepagentsToolGate` 新增 per-tool 结果信任下限，使知识引用按 Claude 路径同样抬高上下文信任水位。
- **策略放行**（`policy/rules.py`）：`knowledge_search_read_rules()` 增加平台自有两个只读工具的 ALLOW，四个 profile 同时生效（同时修复了 Claude 路径的 implicit-deny）。
- **目录声明**（`studio/catalog.py`）：deepagents 能力加 `knowledge`、删对应 limitation；编译器对该组合自动不再报错。共享夹具 `tests/fixtures/runtime/runtime_capabilities_v0.json` 同步，web 契约测试改为断言 deepagents **必须**声明 knowledge。
- 边界保持：导出的独立 DeepAgents 项目仍然无法访问平台知识服务，其 README 的「知识库不随导出」说明未动、仍然为真。

## 测试与门禁

- 新增：`tests/unit/knowledge/test_runtime_bindings.py`（override 优先、信任下限、mode 默认、工具名与策略词表一致）；`tests/unit/runtime/test_deepagents_runtime.py` 增加绑定→平台工具→服务调用→payload 的直测；`tests/unit/policy/test_profiles.py` 断言四 profile 放行平台知识工具；`tests/unit/studio/test_compiler.py` 断言 deepagents+知识库可发布（codex 仍拒绝）。
- 后端全量 unit 1549 passed；integration 314 passed/8 skipped（含 runtime capabilities 契约 7 项）；deepagents 专项 83 passed；claude 路径（sdk_tool_gate/registry/kernels）65 passed。
- 前端全量 Vitest 957 passed/1 skipped（仅测试文件变更，无 web 源码变更，3501 无需重建）。
- Ruff 全绿（剩余 3 条为 automations/projects 测试的既有问题）；Pyright 全仓 1422 错与基线逐条一致，无新增。

## 174 发布

- 仅 API + 三 Worker：`kai/axis-api:develop-7bdc9d81`（image ID `sha256:d436bdc78d7849ffb660b032b5b721ba9650c6f55968e79ed984351e86f4c5ea`，revision label `7bdc9d81e3f07d21aefa03930ba3d0c41c4d999d`），从现行 `develop-953d8cc2` 基座叠加构建。切换前无非终态 Run；`compose.deepagents-174.yaml` 备份 `.bak-7bdc9d81`，发布材料在 `/data/releases/knowledge-deepagents-7bdc9d81/`（mode 700）。无迁移（数据库 `0037`，本镜像 `alembic` 未变）。
- 切换后 api+3 worker 全 healthy、restarts 0、日志 0 错误；runs 状态分布不变；3501（`axis-web-develop-953d8cc2`）与 3301 未动、均 200；173 未操作。
- 镜像内自检：`knowledge_query_tool_name()` 正确；`production-orchestrator` 对该工具 `allow`（规则 `harness-knowledge-query_knowledge_sources`）；目录 deepagents 声明 `knowledge`。
- **线上真实草稿验收**（读 API 容器内 production composition，只读）：`similar-case-analysis-agent` r11（deepagents + `aipolicy`）`validate` → knowledge issues: none，**ready: True**，tenant 级目录已声明能力。发布阻断即用户所见「不支持」提示，已消除。

## 未执行与回滚

- 未为验证发起新的真实模型 Run（改动未授权真实任务；知识检索的真实端到端回答需用户在其草稿里自行触发）。
- 回滚：`cp compose.deepagents-174.yaml.bak-7bdc9d81 compose.deepagents-174.yaml` 后 `docker compose -f compose.yaml -f compose.harbor.yaml -f compose.deepagents-174.yaml --env-file .env.production up -d --no-deps --scale worker=3 api worker`；旧镜像 `kai/axis-api:develop-953d8cc2` 保留。无数据库操作。
