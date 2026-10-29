# ZCode 项目功能逆向与 KAI 复刻（2026-10-17）

## 1. 目标与证据

本地 ZCode Desktop 3.15.1（`/Applications/ZCode.app`，build `f18d6db7`，2026-10-10）的「项目」功能。
产物是 Electron 应用，界面与主进程均打包在 `Contents/Resources/app.asar`；本次先解包到
`/tmp/zcode-asar`，再用 REA 对主进程 workspace 协议与渲染层主 chunk 做静态推导。

| 证据 | 内容 | 位置 / ID |
| --- | --- | --- |
| REA 主进程 | workspace / remote target 的 zod schema、持久化字段 | `ev_0a6f93de290fd87d43611f2aa89304bec65fe985bdc39e1a30375affb61b66ab` |
| REA 渲染层 | 项目侧栏与项目入口所在主 chunk（部分覆盖，资源上限中断） | `ev_e0d5e7d7d9aa972c927fd9231876b21d52fd37ec2c52994ca6b044ab9930ca05` |
| 静态字符串 | 中文/英文 i18n 与 `workspaceSidebar.*`、`workspace.openFolder` | `out/renderer/assets/IntlProvider-DEeEYjpe.js` |
| 静态实现 | 打开目录入口 `V9`、项目移除 handler、add-project 菜单 | `out/renderer/assets/styles-D-FzO2Ov.js` |
| 主进程 IPC | `SelectDirectory` 调用 `showOpenDialog({properties:["openDirectory","createDirectory"]})`；`zcode:open-workspace` / `open-workspace-path` / `workspace-list-request` | `out/main/index.js`、`out/main/chunk-KL4L7SET.js` |

REA 的覆盖限制已保留：渲染层 chunk 在分析中触发 resource constraint，结论只用于定位，不作为运行时证明；
ZCode 的实际行为来自静态 schema、i18n 文案与入口代码的交叉核对。

## 2. ZCode 的项目是什么

### 2.1 数据模型：项目 = 打开的工作目录

主进程的 discriminated union 明确区分：

```ts
// local
{ kind: "local", workspacePath, workspacePurpose: "project" | "conversation" }
// remote (ssh / wsl / docker / server)
{ kind: "remote", workspacePath, localWorkspacePath?, workspaceIdentity?,
  target, lastOpenedAt, lastConnectionStatus: "connected" | "failed",
  lastConnectionError? }
```

同时有一份「已打开项目」的持久化记录：

```ts
{ id, workspacePath, localWorkspacePath?, workspaceIdentity?, target,
  lastOpenedAt, lastConnectionStatus, lastConnectionError? }
```

- `workspacePurpose = "conversation"` 是「不在项目中工作」的会话工作区，对应侧栏的「任务」区；
  `project` 才是用户打开的项目。
- 项目身份是目录路径（本机）或 `workspaceIdentity`（远程），不是数据库里一条只有名字的记录。

### 2.2 添加入口：打开文件夹 / 远程连接

侧栏「项目」标题右侧的「＋」打开菜单，只有两项：

- `workspace.openFolder`：调用 `selectDirectory()`，即原生目录选择器
  （主进程 `SelectDirectory` → `showOpenDialog({ properties: ["openDirectory", "createDirectory"] })）；
  选中后 `onSelectProject(path)` 把该目录加入项目列表。Web/远程形态下可退回服务端目录浏览器。
- `remote.trigger`：SSH/WSL/Docker/Server 远程工作区。

因此 ZCode 没有「只填名字创建空项目」的语义。

### 2.3 侧栏：项目在上、任务在下

`workspaceSidebar.*` 文案与实现给出的能力：

- `projectsSection = 项目`、`conversationsSection = 任务`、`addProject = 添加项目`、
  `noProjects = 尚未打开项目`。
- 视图：`organizeByProject = 项目`、`organizeChronologicalList = 时间线`；
  排序：`sortByCreated = 创建时间`、`sortByUpdated = 更新时间`；
  `expandAllGroups` / `collapseAllGroups`、任务搜索、归档任务。
- 项目行有任务数、未读指示、运行中状态；项目可折叠、可拖拽排序。
- 任务可以「不在项目中工作」，也可以 `detachProject`「取消选择当前项目」。

### 2.4 移除项目：停运行、留历史、不删文件

项目移除入口的确认文案：

- `removeRunningWorkspace.title = 移除运行中的项目？`
- `removeRunningWorkspace.description = 该项目还有运行中的对话或 Agent。移除项目会停止并释放相关运行状态，历史任务不会被删除。`
- 普通移除的说明：`项目“{name}”会从侧边栏移除，但不会删除磁盘上的文件。`

实现上先检查运行中的任务、确认后关闭 workspace tab，并清理该 workspace 的 task service 状态；
不是删除目录，也不是删除历史。

### 2.5 项目级资源随目录走

ZCode 把以下资源放在 workspace 作用域：

- 记忆：`settings.memory.viewer.projectsDescription = 选择一个项目，查看该项目保存的全部记忆`
- 技能 / MCP / 命令 / 插件：导入面板明确区分 `scope.global` 与 `scope.project`，目标也有
  `导入到全局` / `导入到项目`
- 工作流：`workflows.hub` 按项目列出，支持 `promoteToGlobal` / `moveToProject`
- 自动化：`automations.workspace.label = 项目`

这些资源不是 UI 装饰：它们由 workspace 目录内的项目级配置承载，任务在该项目下运行时生效。

## 3. KAI 的差距与复刻口径

KAI 当前（develop `dc452ba2`）已有真实项目实体：`projects` 表、`agui_thread_bindings.project_id`、
`/v1/studio/projects`、侧栏「项目 + 任务」与项目内新建任务。差距是：

| ZCode | KAI 复刻前 | 本次口径 |
| --- | --- | --- |
| 项目 = 本地/远程目录 | 只有名称 | **不复刻本地目录选择器**：KAI 是服务端 Web，浏览器无法安全浏览服务端目录；改为项目级上下文资源 |
| 项目说明 / 项目级指令 | 无 | `instructions`，每个新 Run 捕获一次 |
| 项目记忆 | 无 | `project_memories` 表，用户维护，每个新 Run 注入 |
| 项目文档 | 无 | `project_documents` 表，引用已上传的 input artifact；每个新 Run 自动 stage 到 workspace |
| 项目设置 / 移除 | API 有 rename/archive/remove，无 UI | 项目行「⋯」→ 项目设置；移除保留任务历史 |
| 项目级技能 | 无 | 平台 Skill 包 → 项目引用 → Run 时 staging（见 §7.2） |
| 项目文件查看 | 无 | 文档列表 + 安全 inline 查看（见 §7.1） |
| 项目级 MCP / 工作流 | 无 | MCP 先做 Agent 可见性（§7.3）；项目级 MCP、工作流未做 |

### 3.1 为什么用「附件」承载项目文档

ZCode 的项目文档是工作目录里的文件，Agent 直接读写。KAI 的等价物是 Run workspace 里的输入文件：

- 项目文档 = 已上传的 `InputArtifact` 引用，不复制内容；
- 每次 Run 创建时把项目文档 ID 写进 `run.input["project_document_ids"]`；
- Orchestrator 与用户本轮附件合并后统一 `stage_for_run`，落到 `workspace/inputs/...`；
- 与用户消息附件分开存储，避免每个历史消息都把项目文档显示成「用户上传的附件」。

### 3.2 注入点统一在 `context_projection`

KAI 的三个运行时（Claude Agent SDK / Codex app-server / DeepAgents）都已消费
`RuntimeContext.context_projection`。本次把项目说明与项目记忆格式化后放进同一字段：

```
<project_context project="金融办">
## 项目说明
...
## 项目记忆
- ...
</project_context>
```

Run 创建时一次性捕获，写入 `run.input["project_context"]`；之后修改项目不会改写历史 Run。
Orchestrator 额外发 `context.project.loaded` 事件，payload 只含项目 ID 与记忆/文档数量，不含内容。

## 4. 实现映射

### 后端

- `src/harness/projects/models.py`：`Project.instructions`、`ProjectMemory`、`ProjectDocument`、
  `ProjectResources`、`ProjectRunContext`，以及请求体上限（说明 20k、单条记忆 4k、记忆 200 条、文档 10 个）。
- `src/harness/storage/models.py`：`ProjectMemoryRow`、`ProjectDocumentRow`。
- `migrations/versions/0040_project_resources.py`：两张表。
- `src/harness/projects/repositories.py` / `pg_repositories.py`：项目资源的内存与 PostgreSQL 仓储。
- `src/harness/projects/service.py`：
  - `add/update/delete_memory`
  - `attach/detach_document`（通过 `document_resolver` 校验附件归属与就绪状态）
  - `context_for_run`（说明 + 记忆 + 文档 ID；项目已删除时返回 `None`）
  - 删除项目时先解绑任务，再清理项目资源。
- `src/harness/projects/api.py`：
  - `GET /v1/studio/projects/{id}/resources`
  - `POST/PUT/DELETE /v1/studio/projects/{id}/memories[/{memory_id}]`
  - `POST/DELETE /v1/studio/projects/{id}/documents[/{input_artifact_id}]`
- `src/harness/agui/service.py`：`AguiThreadBinding.project_id` 透传；Run 创建时解析项目上下文，
  校验用户附件 + 项目文档的总数/归属，分别写入 `input_artifact_ids` 与 `project_document_ids`。
- `src/harness/worker/orchestrator.py`：合并两类附件后 stage；把项目上下文拼进 `context_projection`。
- `src/harness/composition.py` / `src/harness/api/dependencies.py`：Postgres / 内存两套装配与附件校验回调。

### 前端

- `web/harness-console/src/lib/studio-client.ts`：`ApiProject.instructions`、项目资源类型与客户端方法。
- `web/harness-console/src/components/project-settings-dialog.tsx`：项目名称、项目说明、项目记忆（增删改）、
  项目文档（上传/移除）、移除项目（二次确认）。
- `web/harness-console/src/components/task-sidebar.tsx`：项目行新增「⋯」设置入口。
- `web/harness-console/src/app/web-codex.css`：项目行两个 26px 操作按钮的定位、深浅色样式。

## 5. 验证

已运行：

```bash
# 后端
PYTHONPATH=src pytest tests/unit/projects -q                              # 13 passed
PYTHONPATH=src pytest tests/unit/agui/test_project_context.py -q         # 2 passed
PYTHONPATH=src pytest tests/unit/agui tests/integration/agui/test_project_task_create.py -q  # 70 passed
ruff check <changed backend files>                                       # All checks passed

# 前端
npx vitest run tests/task-projects.spec.ts tests/sidebar-state.spec.tsx  # 12 passed
npx tsc --noEmit                                                         # 仅既有 docx-preview / pptx-preview 缺模块
```

未运行：完整 `next build`、真实 PostgreSQL 迁移、174 部署与浏览器视觉验收。原因：当前工作树使用
主工作区的 `node_modules`（Next 16.3.3，develop 声明 16.3.8），不应据此声称生产构建通过。

## 6. 已知边界与下一步

- **没有本地/远程目录选择器**：KAI 是服务端产品，浏览器不能直接打开服务端目录；若以后做本地桌面端，
  可以按 ZCode 的 `workspacePath` 模型增加 local target，而不是把路径当普通字符串存。
- **项目记忆是用户维护的**：没有像 ZCode 那样从运行中自动提取项目记忆；KAI 现有 memory_bank 是
  user+agent 维度，扩到 project 维度需要改检索、工具与 consent。
- **项目文档上限 10 个**：与单次任务附件共用 `max_files_per_run`；项目文档 + 本轮附件超过上限时，
  Run 创建阶段会失败并给出附件数量错误，而不是静默丢弃。
- **项目技能已支持**（见 §7.2），但只接平台 Skill 包；项目级工作流 / 自动化仍未复刻。
- **MCP 改为 Agent 可见性**（见 §7.3），不是项目级 MCP；项目级 MCP 需要运行时叠加服务器，未在本次范围。
- 项目说明与记忆是用户提供的内容，按用户指令对待；文档内容仍走现有输入处理与信任边界，不因来自项目而升级为指令。

## 7. 第二批：项目文件查看、项目技能、MCP 可见性

按“先按 ZCode 的交互形式，不扩大成 Manus 项目空间”的范围追加：

### 7.1 项目文件可查看

- 项目设置里的文档行新增「查看」：`/api/input-artifacts/{id}/content?inline=1`。
- 服务端只对 `text/*`、`image/*`、`application/pdf` 使用 `inline`，其余（HTML/SVG 等）仍强制下载；同时返回
  `X-Content-Type-Options: nosniff`，避免用户上传内容以同源页面执行。
- 文件仍以 input artifact 为唯一内容源，项目只保存引用，不在项目表里复制字节。

### 7.2 项目技能

- `Project.skillReferences` 保存 `packageId@revision`，指向平台 Skill 包；写入时校验包存在、修订号一致，并限制 20 个。
- Run 创建时把引用写进 `run.input["project_skill_references"]`；Orchestrator 在 Agent 自带 Skill 之后通过
  `stage_project_skill_snapshots` 把项目 Skill 写进 `.claude/skills`，Codex 再镜像到 `.agents/skills/harness-*`。
- 同名冲突时 Agent 自带 Skill 优先，项目副本记为 `skipped`，并发出 `project.skills.staged` 事件；不静默替换。
- Skill 文件使用与编译器相同的 `SKILL.md` frontmatter 和哈希算法，保证同一平台包在 Agent 和项目两条路径上指纹一致。
- 项目设置里新增「项目技能」多选，直接列平台 Skill 目录（显示名称、摘要、兼容运行时、`packageId@revision`）。

### 7.3 MCP 可见性

问题：构建器里的 `options.mcp` 来自整个能力目录，所以所有智能体都能看到所有 MCP。

改动：

- `McpCapability` 新增 `visibility`（`all` / `selected`）和 `visibleAgentIds`；
- 构建器与集成抽屉都按当前草稿的稳定 `agentId` 过滤：`selected` 时只有被授权的智能体能看到；
- 编译器在 `mcpServers` 校验里新增 `mcp_server_not_visible`，即使绕过 UI 也不能把未授权 MCP 发布出去；
- MCP 注册/编辑表单新增「可见智能体」：默认 `selected`，从智能体打开时预选当前智能体；管理页可显式改成「全部智能体」；
- MCP 详情显示「可见智能体：N 个已授权 / 全部智能体」；
- 旧数据与平台内置 MCP 的模型默认仍是 `all`，避免升级后现有智能体突然失去工具；新建 MCP 默认 `selected`。

边界：这是**构建期可见性与发布校验**，不是运行时网络隔离；运行时仍按 Execution Profile、凭据和 MCP 允许清单执行。

### 7.4 验证补充

```bash
PYTHONPATH=src pytest \
  tests/unit/projects tests/unit/agui/test_project_context.py \
  tests/unit/studio/test_project_skills.py tests/unit/studio/test_compiler.py \
  tests/unit/worker/test_orchestrator.py tests/unit/worker/test_input_staging.py -q
# 121 passed

npx vitest run tests/task-projects.spec.ts tests/mcp-visibility.spec.ts \
  tests/sidebar-state.spec.tsx tests/studio-client.spec.ts
# 39 passed, 1 skipped

npx tsc --noEmit
# 仅既有 docx-preview / pptx-preview 缺模块
```
