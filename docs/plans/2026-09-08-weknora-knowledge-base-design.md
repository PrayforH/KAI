# WeKnora 知识库集成详细设计（功能 1-5）

- 日期：2026-09-08
- 分支：`feature/weknora-knowledge-base`（基于 develop @ 0e6dfb4）
- 参照实例：174 WeKnora `http://172.20.109.174:8180`（服务账号见 `.env`，不入库）
- 前置评估：[2026-08-11-weknora-complex-agent-orchestration-assessment.md](2026-08-11-weknora-complex-agent-orchestration-assessment.md)（结论：双平面组合，WeKnora 管知识数据面，Harness 管执行控制面）
- 关联需求（本设计范围 = 需求 1-5）：
  1. 知识库管理：卡片式统一管理 rag / wiki / rag+wiki 混合三类知识库（P1）
  2. RAG 知识库：后台替换为 WeKnora，支持切片、向量构建、状态跟踪、切片详情查看（P1）
  3. Wiki 知识库：接入 WeKnora wiki 与 rag+wiki 混合搜索，支持切片、wiki 索引构建与展示（摘要/实体/概念抽取）、图谱渲染（P1）
  4. 知识库权限管理：基于 IDAAS 用户组织机构目录树配置使用权限，支持批量添加成员、按成员配置查看/编辑权限（P1；**2026-09-08 决策：首期不对接 IDAAS**，拆期见 §3.4）
  5. RAG 知识库问答适配：问答召回文本切片，回复中可点击查看引用切片详情（P1）
- 非目标（本轮不做，仅预留接口）：需求 6 wiki 问答高亮/图谱跳转、需求 7 智能体问答适配（基本现成）、需求 8 历史对话管理（基本现成）、需求 9 自定义选择 skills（P2）。

---

## 1. 174 WeKnora 实例考察结论（2026-09-08 实测）

### 1.1 实例与数据

- 账号 `xiaokai@shdata.com` 为租户 10000（`xiaokai's Workspace`）owner、系统管理员。
- 现有 3 个知识库，均为 `type: document`；图谱验证库已启用 Wiki 索引策略（19 个 wiki 页面、42 条链接）。
- 模型清单：Embedding `bge-m3-v2`（内置 builtin-bge-m3-v2）、Rerank `bge-reranker-v2-m3`、KnowledgeQA `deepseek-v4-flash / Qwen3-32B / MiniMax-M3`、VLLM `Qwen3.6-27B / MiniMax-M3`。
- 默认分块配置：`chunk_size 4000 / overlap 100`，separators `\n\n`、`\n`、`。`。

### 1.2 KB 数据模型（实测字段）

```
KnowledgeBase: id, name, description, type(document|faq),
  capabilities: { vector, keyword, graph, wiki, faq },
  indexing_strategy(RAG 检索 | Wiki 知识库),
  embedding_model_id, chunking_config{chunk_size, chunk_overlap, separators, parser_engine_rules},
  extract_config{enabled, custom_instructions, nodes[], relations[]},   # 知识图谱抽取
  asr_config / image_processing_config / faq_config,
  chunk_count, creator_id/creator_name, is_pinned, is_processing, is_temporary
Knowledge(document): id, kb_id, title, description, source, channel,
  parse_status, summary_status, enable_status, embedding_model_id,
  file_name/file_type/file_size/file_hash/storage_size, metadata{...process_overrides}
Chunk: id, seq_id, knowledge_id, knowledge_base_id, content, tag_id
WikiPage: id, slug(entity|concept|summary|index)/..., title, page_type, status,
  content(含 [[wikilink]]), summary, aliases[], folder_id, category_path[], wiki_path, depth
```

要点：**WeKnora 的"类型"是 `type(document|faq) + 索引策略(RAG|Wiki) + capabilities 开关` 的组合**，不是三个独立 KB 类型。我们产品语境的 rag / wiki / hybrid 三类映射见 §3.2。

### 1.3 关键 API（已逐一验证）

| 能力 | 端点 | 说明 |
| --- | --- | --- |
| 认证 | `POST /api/v1/auth/login`、`POST /api/v1/auth/refresh` | 返回 JWT `token/refresh_token` |
| KB CRUD | `GET/POST /api/v1/knowledge-bases`、`GET/PUT/DELETE /:id`、`POST /:id/duplicate`、`PUT /:id/pin` | 列表/创建/配置 |
| 混合检索 | `POST /api/v1/knowledge-bases/:id/hybrid-search`（body `query_text`） | 向量+关键词 |
| 文档 | `POST /knowledge-bases/:id/knowledge/file|url|manual`、`GET /knowledge/:id`、`GET /knowledge/:id/stages|spans`、`POST /knowledge/:id/reparse|cancel-parse`、`GET /knowledge/:id/preview|download` | 上传/状态/重解析 |
| 切片 | `GET /api/v1/chunks/:knowledge_id`、`GET /chunks/by-id/:id`、`PUT/DELETE /chunks/:knowledge_id/:id`、`POST .../revert`、`GET .../revisions` | 切片 CRUD+版本 |
| Wiki | `GET/POST /api/v1/knowledgebase/:kb_id/wiki/pages`、`GET /pages/*slug`、`PUT/DELETE`、`GET /index`、`GET /folders`、`GET /revisions/*slug`、`POST /revert`、`GET /graph`、`GET /stats`、`GET /search` | wiki 页面/索引/图谱/统计 |
| 检索测试 | `GET /api/v1/knowledge/search` | 站内检索调试 |
| 会话问答 | `POST /api/v1/sessions`、`POST /api/v1/sessions/:id/knowledge-qa`（KnowledgeQA）、`.../agent-qa`、`GET /continue-stream/:session_id` | WeKnora 原生问答 |
| 组织与共享 | `POST/GET /api/v1/organizations`、`/:id/members`、`/:id/invite`、`PUT /:id/members/:tenant_id`、`POST /knowledge-bases/:id/shares`（kbShares）、`PUT/DELETE /shares/:share_id` | 空间/成员/KB 共享 |
| 模型 | `GET/POST /api/v1/models` | 模型配置 |

### 1.4 UI 页面结构（截图考察）

- **知识库管理页** `/platform/knowledge-bases`：左侧导航（新对话/知识库/智能体/共享空间 + 按时间分组的会话历史）；右侧过滤页签（全部/收藏/最近/本空间）；卡片分组「我创建的 N」「本空间 · 其他成员 N」；卡片含类型图标、名称、描述、文档数、能力图标（图谱/多模态）、创建者。
- **KB 详情页** `/platform/knowledge-bases/:id`：顶部面包屑 + **文档 / Wiki / 图谱** 三个视图页签 + 设置齿轮。
  - 文档视图：搜索 + 标签/类型/解析状态/来源/时间过滤，上传提示"点击或拖拽上传，多格式文档自动解析并智能分块"；文档卡片（标题/摘要/时间/类型）。
  - 文档抽屉：基本信息（创建时间/类型）→ 摘要 → 文档内容（共 N 个分段，**全文 / 查看分块**切换），分块以"片段 N"卡片展示。
  - Wiki 视图：左栏 索引/日志 + 分类目录树（涉案主体 2、法律概念 3）+ 概念页列表；右栏索引页（摘要(2)/实体(2) 分节，`[[wikilink]]` 互链）。
  - 图谱视图：力导向图，图例 **摘要(蓝)/实体(绿)/概念(橙)**，控件（适应屏幕/隐藏箭头/全库概览"19/19 个节点"）；明确提示"Wiki 页面引用关系图 ≠ 知识库设置里基于 LLM 抽取的实体-关系图谱"。
- **KB 设置弹窗**：左栏 基本信息/模型配置/向量存储 | 解析引擎/分块设置/图像处理/音频处理/知识图谱/高级设置 | 存储引擎/数据源 | 共享管理 | 活动记录。
  - 基本信息：知识库 ID（API 集成用）、知识库类型（文档/问答）、**索引策略（RAG 检索 / Wiki 知识库）**、提取粒度；已有内容后索引策略不可改。
  - 知识图谱：启用实体关系提取开关 + 额外提取要求（4000 字以内自定义指令）+ 关系类型。
  - 共享管理：共享到共享空间（空间制，非逐成员 ACL）。
- **共享空间页**：创建/加入空间，空间内共享知识库与智能体；成员角色 Viewer/Contributor/Editor/Admin（源码 `OrgMemberRole`）。

### 1.5 权限模型差距（对需求 4 的关键结论)

WeKnora 的权限 = **租户角色 + 共享空间(组织)成员角色 + KB 级 share 到 space**，没有"按 IDAAS 组织机构目录树逐成员授权查看/编辑"的产品概念。因此需求 4 的授权模型、组织树选择器、批量成员解析必须建在 AXIS 侧，WeKnora 仅以服务账号被 AXIS 代理访问，不直接暴露给终端用户。

**首期决策（2026-09-08）：不对接 IDAAS。** 需求 4 拆两期：一期基于 AXIS 平台用户目录落地 KB 成员模型、批量添加与查看/编辑授权；IDAAS 组织树对接（实时目录同步、组织继承授权、组织树选择器）延后二期，数据模型一次到位、字段预留，二期只换目录来源不改表。

---

## 2. 总体架构

### 2.1 双平面职责切分

```
┌────────────────────────── AXIS (harness + harness-console) ──────────────────────────┐
│  控制面（保留并扩展）                                                                    │
│  ├ 知识库目录/元数据/卡片管理        knowledge 模块 + studio knowledge 页                 │
│  ├ 权限：KB 成员/IDAAS 组织树/审计   新 kb_members + IdentityDirectoryPort               │
│  ├ 会话/AG-UI/引用展示/切片抽屉      agui + agent-thread + citation 组件                 │
│  └ Agent 运行时（Claude SDK/Codex） runtime/*，检索以工具形式进入 Agent Loop              │
└───────────────┬──────────────────────────────────────────────────────────────────────┘
                │  WeknoraGateway（HTTP，服务账号 JWT，只允许 AXIS 后端调用）
┌───────────────▼──────────────────────────────────────────────────────────────────────┐
│  数据面 = 174 WeKnora                                                                   │
│  文档解析/切片/向量(bge-m3-v2)/关键词/混合检索/wiki 页面生成/实体-关系图谱/检索测试          │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

- WeKnora 不直接面向终端用户；所有流量经 AXIS 后端代理并做权限校验（复用 `KnowledgeAcl` + 新 `kb_members`）。
- 现有 `harness/knowledge` 的 file/web 连接器、快照、团队空间共享继续有效（legacy 引擎）；新增 `engine: weknora` 的知识库走新网关。**不做存量数据自动迁移**，提供后续导出导入工具位。

### 2.2 新增后端模块 `src/harness/knowledge/weknora/`

```
weknora/
  client.py        # WeknoraClient：登录/刷新 JWT、KB/文档/切片/wiki/graph REST 封装、重试与超时
  gateway.py       # WeknoraKnowledgeEngine：实现 KnowledgeEnginePort（见下）
  mapping.py       # WeKnora 模型 ↔ AXIS 模型映射（parse_status→SyncStatus、capabilities→kb_type 等）
  configuration.py # WeknoraSettings：base_url、service account、超时、缓存 TTL
```

端口协议（挂在 `knowledge/ports.py`，组合根 `composition.py` 注入）：

```python
class KnowledgeEnginePort(Protocol):
    async def create_base(self, spec: EngineBaseSpec) -> EngineBaseRef: ...       # 建 KB（含索引策略）
    async def delete_base(self, ref: EngineBaseRef) -> None: ...
    async def ingest_document(self, ref, upload: DocumentUpload) -> EngineDocument: ...
    async def list_documents(self, ref) -> list[EngineDocument]: ...              # 含 parse/summary 状态
    async def document_progress(self, ref, doc_id) -> EngineProgress: ...         # stages/spans → 进度
    async def reparse(self, ref, doc_id) -> None: ...
    async def list_chunks(self, ref, doc_id) -> list[EngineChunk]: ...
    async def get_chunk(self, ref, chunk_id) -> EngineChunk: ...
    async def search(self, ref, query, *, top_k, mode) -> list[EngineHit]: ...    # rag|wiki|hybrid
    async def wiki_pages(self, ref) -> list[EngineWikiPage]: ...
    async def wiki_index(self, ref) -> EngineWikiIndex: ...
    async def wiki_graph(self, ref) -> EngineWikiGraph: ...                        # nodes/links
```

配置（追加 `.env.example`）：

```
HARNESS_KNOWLEDGE_WEKNORA_BASE_URL=http://172.20.109.174:8180
HARNESS_KNOWLEDGE_WEKNORA_EMAIL=...          # 服务账号，不入库
HARNESS_KNOWLEDGE_WEKNORA_PASSWORD=...
HARNESS_KNOWLEDGE_WEKNORA_TIMEOUT_SECONDS=30
```

### 2.3 三类知识库的类型语义（功能 1 的模型基础）

| 产品类型 | AXIS `kb_type` | WeKnora 侧落地 | 检索路径 |
| --- | --- | --- | --- |
| RAG 知识库 | `rag` | 索引策略=RAG 检索（capabilities: vector+keyword） | `POST /knowledge-bases/:id/hybrid-search` |
| Wiki 知识库 | `wiki` | 索引策略=Wiki 知识库（capabilities: wiki[+graph]） | `GET /knowledgebase/:id/wiki/search` + 页面/索引 |
| RAG+Wiki 混合 | `hybrid` | 同一 KB 双能力：vector+keyword+wiki+graph | AXIS 侧聚合：hybrid-search + wiki/search，RRF 融合去重 |

AXIS 侧 `KnowledgeBase`（`knowledge/models.py:140`）新增字段：`kb_type: Literal["rag","wiki","hybrid"]`、`engine: Literal["legacy","weknora"]`、`engine_ref: str|None`（WeKnora KB id）、`capabilities` 投影（graph/wiki/vector 布尔，供前端卡片渲染）。迁移：`00xx_knowledge_kb_type.py` 为 `knowledge_bases.payload` 加投影列 `kb_type/engine/engine_ref`。

---

## 3. 功能详细设计

### 3.1 功能 1：卡片式知识库统一管理

- **后端**：`GET /v1/studio/knowledge/bases` 响应元素增加 `kb_type/engine/capabilities/document_count/chunk_count/status(同步健康)`；创建接口接受 `kb_type`，`engine=weknora` 时网关先在 WeKnora 建 KB（按 §2.3 映射索引策略与 extract_config）再落库目录。
- **前端**（`web/harness-console/src/app/studio/knowledge/page.tsx` 重构）：
  - 页签过滤：全部 / 我创建的 / 收藏 / 本空间（对齐 WeKnora 卡片页）。
  - KB 卡片：类型徽标（RAG / Wiki / 混合）、名称、描述、文档数、能力图标（图谱 🕸 / Wiki 📄 / 向量）、创建者、同步状态点；hover 出置顶/复制 ID/设置/删除。
  - 新建向导：类型三选一（rag/wiki/hybrid）→ 名称/描述 →（rag|hybrid）分块配置（默认 4000/100）、（wiki|hybrid）抽取粒度与摘要模型 → 创建并跳转详情。
  - 卡片点击进入 KB 详情页（文档/Wiki/图谱三页签，见功能 2/3）。

### 3.2 功能 2：RAG 知识库后台替换为 WeKnora

- **写入链路**：上传走 `POST /knowledge-bases/:id/knowledge/file`（multipart）与 `/url`、`/manual`；AXIS 目录记录 `engine_ref` 并把 WeKnora 文档 id 写入 source payload。
- **状态跟踪**：`GET /knowledge/:id`（`parse_status/summary_status`）+ `GET /knowledge/:id/stages|spans`（解析阶段明细）映射为 AXIS `KnowledgeSyncRun` 记录；前端文档列表显示排队/解析中/摘要中/完成/失败 + 阶段展开（对齐 WeKnora"全部状态"过滤）。轮询由现有 sync worker 承担，TTL 5s（处理中）/60s（稳态）。
- **切片详情**：`GET /api/v1/chunks/:knowledge_id` 代理为 `GET /v1/studio/knowledge/sources/{ref}/chunks`；切片卡片（序号/内容/字数）+ 点开全文。保留并扩展现有 citation 端点（`knowledge/api.py:194`）接受 `weknora:<chunk_id>` 形式的引用键。
- **检索**：`KnowledgeService.search` 按 `engine` 分派：`weknora` → `hybrid-search`（`query_text`），命中含 `chunk_id/content/score/knowledge_id`，AXIS 补权限过滤后返回；`legacy` → 现有 BM25+TF-IDF 路径不变。
- **删除/重解析**：代理 `DELETE /knowledge/:id`、`POST /knowledge/:id/reparse|cancel-parse`；删除 KB 时提示"将同时删除 WeKnora 数据"，双写清理。
- **向量构建**：完全由 WeKnora 承担（`embedding_model_id=builtin-bge-m3-v2`），AXIS 不再为 knowledge 自建向量；AXIS 侧保留 rerank 为 WeKnora 已配 `bge-reranker-v2-m3`（无需自管）。

### 3.3 功能 3：Wiki 知识库（索引、摘要/实体/概念、图谱）

- **建库**：`kb_type in (wiki, hybrid)` → WeKnora 索引策略=Wiki 知识库，`extract_config` 透传自定义抽取指令与提取粒度；文档上传后 WeKnora 自动生成 summary/entity/concept 页面（页面类型实测四种：`summary/entity/concept/index`）。
- **AXIS 代理端点**（前缀 `/v1/studio/knowledge/bases/{ref}/wiki/`）：`GET pages`、`GET pages/{slug}`、`GET index`、`GET folders`、`GET graph`、`GET stats`、`GET search?q=`；写操作（建页/改页/回退）第一期只读，第二期按需开放并镜像审计。
- **前端 KB 详情新增页签**：
  - **Wiki 页签**：左栏分类目录树（`category_path`）+ 页面搜索；右栏索引页渲染（摘要/实体/概念分节，`[[wikilink]]` 渲染为站内跳转）；页面详情抽屉显示 content（Markdown）、aliases、回链来源文档（`slug=summary/<document_id>` 可反查文档）。
  - **图谱页签**：力导向图渲染 wiki graph（`nodes[{slug,title,page_type,link_count}]` + links）。选型 **AntV G6 v5**（力导向/图例/缩略图导航/中文文档完善，bundle ~200KB gzip；备选 reactflow，偏流程图场景）。交互：按 page_type 着色（摘要蓝/实体绿/概念橙，与 WeKnora 一致）、点节点开页面抽屉、"全库概览"一键复位、节点数>500 时开 CDN 分页加载。
- **混合搜索**：`kb_type=hybrid` 的 `search` 并行调 `hybrid-search`（文本切片）与 `wiki/search`（页面命中），按来源加权融合（切片 0.6 / wiki 页 0.4，可配），命中类型标记 `chunk|wiki_page`。

### 3.4 功能 4：知识库权限管理（成员查看/编辑；IDAAS 延后）

**首期不对接 IDAAS**（2026-09-08 决策）：成员授权基于 AXIS 平台用户目录落地"批量添加成员 + 按成员查看/编辑"；组织机构目录树（实时 IDAAS 同步、按组织子树继承授权、组织树选择器）延后二期。数据模型一次到位、字段预留，二期只换目录来源、不改表结构。

- **数据模型**（迁移 `00xx_kb_members.py`）：

```
kb_members(id, kb_reference, subject_type user|org_unit, subject_id, org_path,
           role viewer|editor, granted_by, granted_at, UNIQUE(kb_reference, subject_type, subject_id))
```

- 一期只写 `subject_type=user`（直授）；`org_unit`/`org_path` 字段为二期组织继承预留。effective 权限解析器一期即实现"直授覆盖继承"的骨架（继承分支空实现），二期接入目录后填充。
- 角色仅查看/编辑两档（映射 API reader/writer），owner 沿用创建者。
- **用户目录**：`IdentityDirectoryPort`（`resolve_users(ids|emails)`、`search_users(q)`）一期由平台 `users` 表实现（`src/harness/auth/`）；不引入 `HARNESS_IDAAS_*` 配置。
- **API**（`/v1/studio/knowledge/bases/{ref}/members`）：`GET` 列表、`POST` 批量添加（body: `user_ids[] | emails[]` + `role`，服务端解析为成员，无效邮箱忽略并在响应中返回清单）、`PUT /:member_id`（改角色）、`DELETE /:member_id`。
- **执行点**（后端统一收口在 `KnowledgeService` 的 ACL 复查处）：目录可见性、文档/切片/Wiki/图谱代理读、检索、QA 引用查看，全部要求 effective ≥ viewer；写操作 ≥ editor。WeKnora 侧不感知终端用户（服务账号），权限完全由 AXIS 代理层裁决。
- **前端**：KB 设置新增"成员管理"页签——用户搜索多选 + 粘贴邮箱批量添加（选"查看/编辑"角色）；成员表（角色下拉、移除）。页签左侧预留组织树位置，二期接入 IDAAS 后启用。
- **与现有模型关系**：`KnowledgeAcl`（tenant/restricted + user/workload 白名单）保留作为粗粒度开关；`kb_members` 为 KB 级细粒度层；团队空间 `SharedKnowledgeBase` 不变。
- **二期预留（IDAAS，另立计划）**：HTTP 适配器实现 `IdentityDirectoryPort` 的 `get_org_tree()`、`list_users_under(path, recursive)`；配置 `HARNESS_IDAAS_BASE_URL/TOKEN`；成员管理页左侧组织树（懒加载/搜索、勾选组织子树批量授权）；`org_unit` 继承授权生效。API 契约与表结构不变。

### 3.5 功能 5：RAG 知识库问答适配（引用切片点击查看）

- **召回链路（默认，AXIS 原生）**：扩展 `knowledge/runtime.py` 的 `query_knowledge_sources` 工具——会话绑定含 `engine=weknora` KB 时走网关检索，返回结构化命中：`{chunk_id, knowledge_id, kb_ref, document_title, content, score, citation_index}`。Agent 按引用序号在回复中标注 `[1][2]`。
- **引用传递**：Run 事件（`agui/activity.py`）新增 `citations` 附件随 TOOL_CALL/RunFinished 事件下发；前端 `task-history` 缓存随线程持久化，刷新后引用可回放。
- **前端交互**（`agent-thread.tsx` + 新 `citation-chip.tsx`）：回复文本中的 `[n]` 渲染为引用徽标；点击打开右侧抽屉（复用文档抽屉模式）：文档标题/所属 KB/切片序号 + 切片全文 + "在知识库中打开"跳转 KB 详情对应切片；打开前经 `GET /sources/{ref}/chunks/{chunk_id}` 校验查看权限。
- **deerflow 备注**：需求括注"deerflow连weknora"。本设计默认走 AXIS 运行时 + WeKnora 网关（复用 AG-UI、审批、引用渲染，无第三运行时）。若后续确认引入 deerflow 做 deep research，它只作为受控外部执行器消费同一 WeknoraGateway，不并联第二条问答链路。
- **验收基准**：同一问题下，引用切片与 WeKnora 控制台检索测试结果一致；无 viewer 权限的用户点击引用返回 403 并隐藏内容。

---

## 4. 实现计划（功能 1-5）

> 里程碑按依赖排序；估算为单人·人日。详细任务拆分见配套计划文档
> [2026-09-08-weknora-knowledge-base-implementation-plan.md](2026-09-08-weknora-knowledge-base-implementation-plan.md)。

| 里程碑 | 内容 | 覆盖需求 | 估算 | 依赖 |
| --- | --- | --- | --- | --- |
| M1 网关与 RAG 后台 | WeknoraClient/端口/配置/组合根；KB+文档+状态跟踪+切片代理；检索分派 | 需求 2 | 6d | 无 |
| M2 目录与卡片 | kb_type 模型+迁移；bases API 扩展；卡片页/新建向导/KB 详情(文档页签) | 需求 1(+2 UI) | 4d | M1 |
| M3 Wiki 与图谱 | wiki 代理端点；Wiki 页签(目录树/索引/页面抽屉)；G6 图谱页签；hybrid 聚合检索 | 需求 3 | 6d | M2 |
| M4 权限与问答引用 | kb_members 迁移+API+平台用户目录+成员管理 UI；query 工具结构化引用+citation chip+切片抽屉 | 需求 4、5 | 5.5d | M2 |
| 验收联调 | 174 实例端到端：建 rag/wiki/hybrid 库→上传→状态→切片→wiki/图谱→权限→问答引用 | 全部 | 2d | M1-M4 |

风险与开放问题：

1. **IDAAS 延后（2026-09-08 决策）**：一期权限基于平台用户目录（搜索多选/邮箱批量），组织树批量授权与继承延后二期；`IdentityDirectoryPort` 即二期接入缝，IDAAS 接口规格问题随二期另立计划关闭。一期代价：授权入口从"按组织勾选"退化为"按用户勾选/粘贴邮箱"，大范围授权操作成本略高。
2. WeKnora 索引策略建库后不可改（实测提示），产品需明确 hybrid=建库时双开，而非事后切换。
3. WeKnora 版本升级（当前未知版本号，API 以本次实测为准）可能变动路由——网关层做能力探测（`GET /knowledge-bases` 响应字段存在性）。
4. G6 引入增加 ~200KB 前端 bundle，需在 `web-build` 门禁中确认预算。
5. 服务账号模式意味着 WeKnora 侧审计全部记在服务账号名下，AXIS 侧审计必须补齐（复用 audit_logs）。
