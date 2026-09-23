# 173 Builder 布局与运行环境修复

## 最终布局

- 中间栏的「配置 / 代码」切换配置卡片和项目代码目录；点击代码文件或展开按钮，在右侧显示源码，收起恢复对话。
- 对话文件列表保留在右侧 WorkbenchRail；打开文件时同步收起中间配置栏。重新展开配置会关闭文件栏，保留对话与未发送文字。文件与代码视图共用展开/收起图标样式。
- 配置卡片的数量独立于标题/描述，和展开箭头在同一行居中；Header、配置切换栏、对话栏和代码工具栏对齐。
- 代码区移除重复的「收起代码」文字按钮，使用目录栏的展开/收起图标。
- 顶部压缩为两行：项目操作、当前文件操作；目录对应配置切换、目录操作。全部代码/本次改动/搜索使用带可访问名称和提示的图标，搜索在同一栏内展开，关闭时清除筛选。修订信息移至简短标记和底部状态栏，差异视图不再增加说明栏。
- 内置工具、MCP、知识库、技能、Python 算子分别进入对应抽屉；工具按公开联网、文件与命令分组，去掉账号联网状态及设置链接说明。
- MCP 注册抽屉保留背景工作区，默认仅显示名称、地址和鉴权方式；引用标识自动生成，其他字段收进高级设置。检测错误就地显示，注册成功刷新列表，不自动绑定。
- HTTP 环境使用 `crypto.getRandomValues()` 生成 MCP 引用标识，避免 `randomUUID()` 不可用导致整个页面报错。
- 系统提示词默认编辑区域加高；Python Schema 与源码上下排列；Subagents 按钮和选择框统一尺寸，删减说明文字。
- 右侧文件展开时缩小对话内边距和欢迎标题，避免窄区域挤压。

参考 [Agenta 的 AgentIntegrationDrawer](https://github.com/Agenta-AI/agenta/blob/main/web/packages/agenta-entity-ui/src/DrillInView/SchemaControls/agentTemplate/AgentIntegrationDrawer.tsx) 的分类资源选择方式，沿用项目主题和保存逻辑。

## 流式输出与共用范围

Builder 的待确认配置建议通过公共 composerAccessory 扩展槽展示在输入框上方。支持逐项勾选、展开编辑、查看修改前的值、查看代码差异和应用。仅提交所选修改，保留目录修订元数据；差异预览、后续 Builder 上下文与应用使用同一份编辑结果。空选择、过期草稿和执行中状态会阻止不适用的操作，失败后保留选择与内容。

补齐「显示名称」「配置修改建议」「修改配置：…」等明确配置意图，避免误入普通业务试跑。

Builder 后端已经返回真实 SSE 增量。前端将流式消息与最终消息保留为同一个消息 ID，进度状态独立展示，避免结束时替换消息节点以及把进度文字当作答案。

主对话、Builder 和效果验证复用输入、消息展示等组件，但尚未完全统一运行协议。主对话和效果验证使用运行事件；Builder 保留独立构建接口和可审核的配置变更。文件上传、下载、预览有共用基础能力，但运行产物、智能体内部文件和生成源码仍来自不同数据源。本次没有将这些接口合并为一个内核。

## 运行环境修复

173 evolution 原配置缺少 WeKnora 连接参数，并使用旧 Cube 模板。已从 174 同步沙箱与知识库配置白名单，包括连接配置、模型标识、Cube 模板及执行模式；173 自身数据库、Redis、MinIO 和应用认证保持原配置。

- 知识库创建捕获 KnowledgeEngineError，通过已有错误翻译返回明确的 503/502，避免缺配置时出现未处理的 500。
- 修复已发布 Python 算子的执行路由：具有精确匹配的已验证 bundle 快照的算子走 deferred Cube 执行器，不再仅因包含 python_entry 就强制进入不支持该 SDK 工具的完整远端 CLI 路径。
- 任意导入式 Python 及其他需要远端 CLI 的能力仍保留原有约束；没有启用不安全的本地 Python 执行。

## 同步 develop 的 DeepAgents 项目目录

从 `origin/develop`（检查时为 `3cb27d30`）同步 `2e169a88` 和 `499f0368`，在当前分支对应 `f9781c53` 和 `47edcee2`。只同步导出工程与相关测试，没有覆盖 Builder 和 173 的修复。

导出工程使用 `src/sapling_deep_agents`，包括 agents、config、prompts、controller、run、middleware、services、skills、tools；保留根 agent.py 兼容入口，带 Docker/Compose、测试及工程配置。预览默认选中包内主装配文件 `src/sapling_deep_agents/agents/agent.py`。

173 实际预览与下载均 HTTP 200，样本 39 个文件，元数据 `projectLayout=src-v1`；每个文件的摘要和 ZIP 内容一致，全部 Python 文件可解析，用户草稿仍为 r4。ZIP SHA-256：`b7b1cf5fef71dc4be3d2d4a4f84594926f3f5947b9877b3ab295892994725e6f`。

## 发布记录

- 分支：`auto/agent-evolution`。
- Web / API 部署代码：`0618558c003b4ad3d45834676d02d045a7d491d3`；Worker 保持 `93650fdb334322b59275ace20b846c1fd3030708`。
- 地址：`http://172.20.109.173:3302`。
- Compose 项目：`agent-evolution-173`，目录：`/data/agent-studio-evolution-20260920`。
- Web：`kai/axis-web:evolution-builder-0618558c`，Build ID：`TVNirwUpDhgQKZl3LWSzi`。
- API：`kai/axis-api:evolution-builder-0618558c`；Worker：`kai/axis-api:evolution-builder-93650fdb`。导出功能本轮仅重建 API，Worker 容器 ID 前后相同。
- Web、API、Worker 均 healthy；登录页与 API health 返回 200。
- 174 环境本次只读取配置，没有部署修改。

Web 在本地完成 webpack standalone 构建，在服务器保留 Linux 依赖构建 COPY 层；先通过无网络临时容器路由检查，再切换服务。API 基于既有依赖镜像覆盖源码，未变更数据库结构。

API / Web 镜像已标注本次源码 revision；Worker 旧镜像的 OCI 标签继承自基础镜像，识别 Worker 应以镜像 tag 和部署记录为准。

## 验证结果

- 最终前端全量：131 个测试文件，840 passed / 1 skipped；最终生产构建与 TypeScript 检查通过。
- DeepAgents 同步回归：79 项导出、代码预览及 Builder/API 测试通过；指定独立运行环境后，5 项真实框架执行/打包测试全部通过。
- 最后的两栏紧凑布局与新目录已通过组件及线上接口检查；浏览器连接随后出现空白窗口和工具超时，未完成这版的截图复查。此前文件收起、数量对齐和可编辑建议卡片的浏览器验证已完成。
- 173 页面验证：文件打开自动收起配置，返回配置后未发送文字保留；数量与箭头对齐；代码区无重复收起按钮。真实 Builder 请求返回两项建议，取消名称、编辑简介、查看差异并返回后内容保留，最后放弃建议，用户草稿保持 r4。
- 验证时发现旧意图识别把配置建议误路由为试跑，已修正；该次试跑产生的两条测试记忆提案已拒绝，没有生效。
- 后端最终路由相关：141 passed，覆盖 composition、SDK 工具限制、编排及 registry runtime；知识库缺配置错误另有回归覆盖。
- Ruff 与 `git diff --check` 通过。
- 页面检查覆盖 MCP 精简注册及高级设置、配置卡片、代码目录/源码切换、右侧文件栏、加高的系统提示词、精简的 Subagents 和 Python 单列编辑器。最终窄栏标题及选择框样式微调未另做浏览器复查。
- Builder 真实 SSE：HTTP 200，进度约 0.18 秒，首条回复约 1.34 秒，回复增量持续至约 1.69 秒，最终结果约 1.82 秒，共 92 个事件。短答案本身完成很快；没有添加人为打字延迟。组件测试验证流式/最终消息节点稳定、进度不进入答案。
- Cube 临时沙箱通过中文文件传输、stdout/stderr、非零退出、超时恢复及 CLI 协议初始化检查，检查后删除。
- 知识库真实创建 201，列表能查到引擎引用，验证后删除 204。
- 真实效果验证 run：`run_4dc7d6b5495d44c68ad682316ee5fef8`，约 12.2 秒成功。`custom_operator_1(value=42)` 返回 `result=42`，工具事件记录 `cubesandbox-deferred` 与 container 隔离。
- 随后写入并发布 `environment-check.txt`，内容为 `sandbox-ok:42` 加换行。产物 `artifact_017da5e0962a43b5a5a9ee7fe3c659c1` 状态 ready，实际下载 HTTP 200、14 字节，SHA-256 为 `9f146341a01573e799d6a1318f1fb6841956cbe8aeca45912e88524e0c48e32d`。
- 验证没有修改现有用户草稿配置，保留 r4。没有可用真实 MCP 服务，因此验证注册交互与错误展示，没有声称真实 MCP 绑定成功。

## 回滚资料

- 最新 Web 切换前备份：`backups/builder-layout-0618558c/`，可回退至 `kai/axis-web:evolution-builder-0e8ed99a`；本轮交互前镜像为 `kai/axis-web:evolution-builder-93650fdb`。
- 最新 API 备份：`backups/runtime-0618558c/`，可回退至 `kai/axis-api:evolution-builder-93650fdb`，仅回退 API 镜像会恢复旧导出目录。
- 较早 API 备份：`backups/runtime-93650fdb/compose.json`。其中上一版 Python 路由仍存在问题，不应作为已验证正常版本。
- 环境同步前备份：`backups/runtime-06e394af/api.env` 和 `compose-before-runtime.json`。旧环境缺知识库参数且使用旧 Cube 模板，恢复会同时恢复这些故障。
- 需要回滚时按故障组件单独选择镜像及配置，执行对应服务的 Compose 更新，不整体覆盖其他服务或用户数据。
