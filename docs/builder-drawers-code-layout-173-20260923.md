# 173 Builder 布局与运行环境修复

## 最终布局

- 中间栏的「配置 / 代码」切换配置卡片和项目代码目录；点击代码文件或展开按钮，在右侧显示源码，收起恢复对话。
- 对话文件列表保留在右侧 WorkbenchRail，不占用中间配置栏。文件与代码视图共用展开/收起图标样式；文件预览使用当前主题。
- 配置卡片统一图标、标题、数量、描述和箭头的网格位置；Header、配置切换栏、对话栏和代码工具栏对齐。
- 内置工具、MCP、知识库、技能、Python 算子分别进入对应抽屉；工具按公开联网、文件与命令分组，去掉账号联网状态及设置链接说明。
- MCP 注册抽屉保留背景工作区，默认仅显示名称、地址和鉴权方式；引用标识自动生成，其他字段收进高级设置。检测错误就地显示，注册成功刷新列表，不自动绑定。
- HTTP 环境使用 `crypto.getRandomValues()` 生成 MCP 引用标识，避免 `randomUUID()` 不可用导致整个页面报错。
- 系统提示词默认编辑区域加高；Python Schema 与源码上下排列；Subagents 按钮和选择框统一尺寸，删减说明文字。
- 右侧文件展开时缩小对话内边距和欢迎标题，避免窄区域挤压。

参考 [Agenta 的 AgentIntegrationDrawer](https://github.com/Agenta-AI/agenta/blob/main/web/packages/agenta-entity-ui/src/DrillInView/SchemaControls/agentTemplate/AgentIntegrationDrawer.tsx) 的分类资源选择方式，沿用项目主题和保存逻辑。

## 流式输出与共用范围

Builder 后端已经返回真实 SSE 增量。前端将流式消息与最终消息保留为同一个消息 ID，进度状态独立展示，避免结束时替换消息节点以及把进度文字当作答案。

主对话、Builder 和效果验证复用输入、消息展示等组件，但尚未完全统一运行协议。主对话和效果验证使用运行事件；Builder 保留独立构建接口和可审核的配置变更。文件上传、下载、预览有共用基础能力，但运行产物、智能体内部文件和生成源码仍来自不同数据源。本次没有将这些接口合并为一个内核。

## 运行环境修复

173 evolution 原配置缺少 WeKnora 连接参数，并使用旧 Cube 模板。已从 174 同步沙箱与知识库配置白名单，包括连接配置、模型标识、Cube 模板及执行模式；173 自身数据库、Redis、MinIO 和应用认证保持原配置。

- 知识库创建捕获 KnowledgeEngineError，通过已有错误翻译返回明确的 503/502，避免缺配置时出现未处理的 500。
- 修复已发布 Python 算子的执行路由：具有精确匹配的已验证 bundle 快照的算子走 deferred Cube 执行器，不再仅因包含 python_entry 就强制进入不支持该 SDK 工具的完整远端 CLI 路径。
- 任意导入式 Python 及其他需要远端 CLI 的能力仍保留原有约束；没有启用不安全的本地 Python 执行。

## 发布记录

- 分支：`auto/agent-evolution`。
- 部署代码：`93650fdb334322b59275ace20b846c1fd3030708`。
- 地址：`http://172.20.109.173:3302`。
- Compose 项目：`agent-evolution-173`，目录：`/data/agent-studio-evolution-20260920`。
- Web：`kai/axis-web:evolution-builder-93650fdb`，Build ID：`LoBQigJwmlxk8DeRfzQa6`。
- API / Worker：`kai/axis-api:evolution-builder-93650fdb`。
- Web、API、Worker 均 healthy；登录页与 API health 返回 200。
- 174 环境本次只读取配置，没有部署修改。

Web 在本地完成 webpack standalone 构建，在服务器保留 Linux 依赖构建 COPY 层；先通过无网络临时容器路由检查，再切换服务。API 基于既有依赖镜像覆盖源码，未变更数据库结构。

API 镜像继承了基础镜像的旧 OCI revision 标签；识别本次发布应使用上述镜像 tag、部署 revision 文件和镜像摘要，不能仅看继承标签。

## 验证结果

- 前端全量：131 个测试文件，829 passed / 1 skipped；最后的样式微调后生产构建及 TypeScript 检查通过。
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

- 最新 Web 切换前备份：`backups/builder-layout-93650fdb/`。
- 最新 API 切换前备份：`backups/runtime-93650fdb/compose.json`。其中上一版 Python 路由仍存在问题，不应作为已验证正常版本。
- 环境同步前备份：`backups/runtime-06e394af/api.env` 和 `compose-before-runtime.json`。旧环境缺知识库参数且使用旧 Cube 模板，恢复会同时恢复这些故障。
- 需要回滚时按故障组件单独选择镜像及配置，执行对应服务的 Compose 更新，不整体覆盖其他服务或用户数据。
