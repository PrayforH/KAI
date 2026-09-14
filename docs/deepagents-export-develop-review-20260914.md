# DeepAgents 项目导出：develop 合并审查

日期：2026-09-14。范围：将 173 在途 DeepAgents 导出整理为可交付的独立 Python 工程出口，合入 develop。保留现有两个平台运行时，不引入第三个生产 runtime；Tavily 下线和模型配置改动不在本次范围。

## 结论与导出内容

原实现不能直接整体合入：MCP 没有执行接入，部分 Python Schema 无法导入，只读约束在文件中间件覆盖后失效，子智能体仅是职责模板。修复后，已验证的工程导出链路可合并。

ZIP 包含 `pyproject.toml`、`langgraph.json`、同步图工厂、主提示词、原始 Python 算子与完整 JSON Schema、技能文件与二进制附件、MCP 连接配置、固定发布版本子智能体及其独立模型/工具/技能、环境变量模板、README 和 `agent-studio.json`。生成结果固定时间戳，可重复导出。

- 运行时固定 `deepagents==0.7.13`；其他依赖按项目声明的兼容范围安装。
- MCP 工具异步发现并接入模型、工具执行两个环节；遵守目录工具名单、保留平台工具名，查询参数中的凭据正确编码。凭据值不随包导出。
- Python 算子的原始源码独立存放，保留 future imports；包装器使用原始 Schema 并验证布尔、null、嵌套字段、enum 和非 Python 标识符的参数名。
- 无 Bash 时，只读文件权限在实际中间件执行；有 Bash 的只读策略对 execute/write/edit 提供 LangGraph 审批中断。技能为空时也创建 workspace。
- 子智能体取发布注册表的不可变快照，不取当前已编辑的草稿；无法解析的固定版本、嵌套委派、hooks 或缺失源码拒绝导出。关闭隐式 general-purpose 子智能体，避免导出多余委派入口。
- Skill 引用缺失时明确拒绝导出。无效安装版本号、无效 Python 源码和子智能体模块名冲突明确报错。
- `maxTurns` 近似映射为默认 `recursion_limit`；Bash 保留单次超时。

## 明确边界

这是有边界的工程迁移，不是平台语义的无损复制。生成的 README 和元数据列明：知识库检索、平台记忆、原审批记录/策略、评测、配额/遥测、模型路由治理以及未映射平台工具不随包迁移。后台子任务转同步委派；父子使用各自 workspace。LangGraph 服务管理自己的会话与审批，不能续接平台原会话。

使用者配置可访问的模型/MCP 凭据；同一 provider 使用进程级凭据。自定义算子的额外第三方库、系统命令及私有模块需在目标环境补充；导出器不推断 import 名与 PyPI 包名的对应关系。Bash 使用宿主机权限，workspace 不是 Shell 沙箱。

## 验证证据

- Python 全量回归：1389 passed、4 skipped；唯一失败是本次测试命令误选了不存在的 MinIO 桶。改回项目测试桶后单项通过，累计 1390 项通过。另复测了最新 service 修改对应的固定版本子智能体 API。
- Web：89 个测试文件，590 passed、1 skipped；Next.js 生产构建通过。
- Ruff 全项目通过。新增导出器、service 和导出测试的 Pyright 检查 0 errors；项目原有类型问题保留。
- 隔离的真实 DeepAgents 0.7.13 运行测试：MCP 模型→工具调用→结果闭环、目录外工具不暴露；复杂 Schema 与算子执行；只读写入阻断；Bash 审批中断和恢复；真实子智能体委派、技能注入、独立算子加载。
- 新虚拟环境仅安装 ZIP 声明的依赖，editable 安装和 `uv pip check` 通过。含 MCP 子智能体的项目通过 `langgraph dev` 启动、`/ok`、创建 assistant 和读取图 schema。该验证关闭了本机未安装 SOCKS 依赖的代理配置。
- 模型响应与 MCP 发现使用测试替身，验证实际 DeepAgents/LangGraph 执行框架；没有调用付费模型或外部 MCP 服务。
- API 验证导出 ZIP、内容类型/文件名、跨用户 404，以及“发布子智能体后修改草稿，导出仍使用原快照”。原 NexAU 导出回归通过。

运行实测的可复现方式：在独立环境安装导出项目依赖和 `langchain-mcp-adapters>=0.3,<0.4`，设置 `DEEPAGENTS_TEST_PYTHON=/该环境/bin/python`，运行 `tests/integration/studio/test_deepagents_export_runtime.py`。平台本身不安装 DeepAgents。

本次只提交、合并代码。174 保持上一轮已验证的知识库版本；本次没有更新 173/174 镜像。
