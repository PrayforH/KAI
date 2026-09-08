# Agent Studio 长期记忆与 OpenViking 实现分析

分析日期：2026-09-08。

本项目依据当前工作区源码，Git 基线 `56c8531`，包含已有未提交修改；本文没有修改业务实现。OpenViking 依据官方仓库当日主分支快照 `0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf`，并交叉核对官方文档。主分支不等于已发布稳定版本，后续 PoC 应固定版本和镜像摘要。

## 1. 判断与建议

**可以借鉴，且有明确价值。建议保留现有 Memory Bank 的权威记录和治理机制，优先借鉴 OpenViking 的查询相关召回、上下文预算装配、结构化提取与增量合并；把直接接入 OpenViking 作为可替换后端的验证选项。**

当前项目的优势是控制面完整：身份、授权、来源、状态、CAS、保留期限、用户编辑和删除已有代码与测试。主要不足是记忆使用链路：运行时没有根据问题检索，内容组织比较扁平，缺少自动提取和语义更新闭环。

OpenViking 的优势是上下文组织、语义检索、会话沉淀和经验演进；它的身份范围、自动生效语义、原始会话归档与经验指令生成方式，需要适配本项目，不能直接替代现有规则。

第一步应交付“跨会话确实能想起相关事实”的 Memory V2，而不是先迁移所有知识、技能、会话和文件存储。

## 2. 当前项目真实实现

### 2.1 长期记忆与其他持久化上下文的区别

| 组件 | 当前职责 | 与长期记忆的关系 |
| --- | --- | --- |
| Run Input / Event | 当前请求、执行事实与事件 | 提供记忆提取的来源证据 |
| SDK SessionStore / Codex thread | 多轮会话与执行上下文恢复 | 会话恢复不等于跨会话记忆检索 |
| Workspace Snapshot / Artifact | 工作文件恢复与交付物保存 | 文件存在不代表 Agent 会主动召回 |
| ContextService / SessionContextDigest | 会话信任状态、事实、决策、未完成任务、恢复投影 | 是会话级摘要基础，可用于未来提取输入 |
| UserMemoryService | 旧版整段记忆的版本化存储及统一投影入口 | 兼容层仍参与运行时注入 |
| MemoryBankService | 条目化、受控的长期偏好和事实 | 当前长期记忆主体 |
| KnowledgeService | 知识来源、快照、权限、引用与搜索 | 外部知识系统；不能与用户事实或技能指令混同 |

本项目并非只有一张记忆表，也不能因为有 SessionStore 就认为长期记忆已经完整。

源码：[ContextService](</Users/xiaokai/Documents/agent studio/src/harness/context/service.py>)、[设计中的三层上下文](</Users/xiaokai/Documents/agent studio/docs/agent-production-platform-design.md:764>)。

### 2.2 数据与写入路径

`MemoryEntry` 包含：

- 隔离：`tenant_id / user_id / agent_name`；
- 标识与版本：`entry_id / content_hash / version`；
- 内容治理：`content / sensitivity / status / confidence`；
- 来源：`source.kind / label / run_id / session_id / captured_at`；
- 授权与生命周期：`consent_id / created_at / updated_at / expires_at / deleted_at`。

PostgreSQL 使用独立字段支撑范围、状态、版本和过期查询，其余完整对象放在 JSON payload；另有 `memory_consents`、`memory_retentions`。这适合保留为权威账本。

当前主路径：

```text
用户/API 或 Agent propose_memory
  → 规范化 + 敏感分类
  → 禁止内容拒绝
  → 普通提议 pending
  → 用户逐条确认，或已有 Agent 一般信息自动保存授权
  → active
  → 检索/投影
  → 编辑、拒绝、删除或过期
```

Agent 一般信息只有在对应授权有效时自动激活；敏感提议保持待确认。编辑、确认、删除等使用版本条件更新。删除、拒绝、过期会替换正文并清除原内容 hash，而不只是从界面隐藏。

源码：[模型](</Users/xiaokai/Documents/agent studio/src/harness/memory_bank/models.py:74>)、[服务](</Users/xiaokai/Documents/agent studio/src/harness/memory_bank/service.py:50>)、[PostgreSQL Repository](</Users/xiaokai/Documents/agent studio/src/harness/storage/memory_bank_repository.py>)。

### 2.3 检索与注入实际上是两条路径

**控制面搜索：** 查询当前租户、用户、Agent 下最近更新的最多 1000 条 active 记录，在 Python 中排除过期项，再做关键词匹配。英文使用词项，中文使用连续片段和二元片段，词项最多保留 32 个。

分数为 `词项覆盖率 × 0.8 + confidence × 0.2`，没有词项重合就不召回。它不是 embedding 检索，也不是完整 BM25。Agent 提议的 confidence 默认固定为 0.7，不能当作经过校准的事实可信概率。

**运行时注入：** Worker 调用 `UserMemoryService.projection(identity)`；它先取旧版整段文本，再拼接 Memory Bank 最近更新的最多 20 条 active、未过期记忆，最终截取前 4000 个字符。这个调用没有传入当前问题。

这意味着增加语义搜索 Adapter 本身不会改善运行时记忆：必须同时把检索结果接入 Worker 的投影入口。

源码：[搜索](</Users/xiaokai/Documents/agent studio/src/harness/memory_bank/search.py:16>)、[服务检索与投影](</Users/xiaokai/Documents/agent studio/src/harness/memory_bank/service.py:282>)、[4000 字符兼容层](</Users/xiaokai/Documents/agent studio/src/harness/application/memory.py:37>)、[Worker 注入](</Users/xiaokai/Documents/agent studio/src/harness/worker/orchestrator.py:1096>)。

### 2.4 Runtime 能力不完全对齐

- Claude 本地 Runtime 自动挂载进程内 `propose_memory` MCP。
- Claude 远端路径支持带短期工作负载令牌的 HTTP MCP；公开 URL 未配置时不会附加该远端写工具。
- Codex Runtime 读取 `memory_projection`，已有通用 MCP 转换能力；在当前 `RegistryCodexRuntime` 中未看到与 Claude 相同的 MemoryBank/RemoteMemoryMcpProvider 自动附加路径。不能因 Codex 支持 MCP 就认定记忆写入已自动对齐。
- 当前专用记忆 MCP 只暴露 `propose_memory`，没有 `search_memory / read_memory`。Agent 无法通过该工具按需查阅未注入的旧条目。

源码：[Claude 挂载](</Users/xiaokai/Documents/agent studio/src/harness/runtime/claude_sdk.py:857>)、[HTTP Memory MCP](</Users/xiaokai/Documents/agent studio/src/harness/memory_bank/workload.py:126>)、[Codex Registry](</Users/xiaokai/Documents/agent studio/src/harness/runtime/registry_codex_runtime.py:134>)、[Codex Prompt](</Users/xiaokai/Documents/agent studio/src/harness/runtime/codex_runtime.py:526>)。

### 2.5 优势与必须补齐的部分

应保留：服务端身份生成、默认按 Agent 隔离、候选确认、授权策略、来源、CAS、删除清正文、过期维护、用户可见账本、工具审计脱敏，以及不可信上下文禁止写记忆的策略。

需补齐：

| 问题 | 直接影响 | 建议 |
| --- | --- | --- |
| 运行时只读最近 20 条 | 较早的关键事实永远不进本轮上下文 | 问题相关召回 + 少量常驻偏好 |
| 全文硬切 4000 字符 | 后面的条目丢失，数据块可能不闭合 | 按完整条目装配，预算不足时降级 |
| 旧文本先拼接 | 旧记忆可挤掉全部受控条目 | 旧数据迁移并统一治理和预算 |
| 最新 1000 条后才搜索 | 更早内容即使关键词吻合也不可达 | 后端执行范围过滤与 Top-K |
| hash 没有用于去重 | 相同事实反复新增 | 精确幂等去重，再做语义合并 |
| 没有事实键与替代关系 | 新旧偏好冲突时一起生效 | topic/key、适用条件、有效时间、supersedes |
| 提取依赖 Agent 主动调工具 | 对话中的稳定事实容易漏存 | 会话事件驱动的候选提取 |
| 内容与身份范围较单一 | 项目知识、个人偏好、执行经验混装困难 | 类型与 scope 分开建模 |
| 缺少运行时召回明细 | 用户不知道为什么想起或忘记 | 保存选中条目、版本、得分、预算与排除理由 |

另有几项工程边界：过期过滤发生在 LIMIT 后，过期待清理项会占用候选名额；HTTP 请求有 4000 字符限制，但 MCP→Service 路径未统一执行同样的上限；旧 UserMemory 不走 Memory Bank 的状态与到期过滤；当前记忆键没有 Agent owner/stable ID，而执行身份已支持不同 Agent owner，同名 Agent 情形需要明确是否共享。后者是模型设计风险，不是本次发现了真实线上泄露。

安全分类当前以正则为主，数据块转义与工具策略是其他防线。现有禁止样例通过，不代表自然语言提示注入已被完全解决。

## 3. 本地验证结果

运行已有记忆服务、应用层、工具、Runtime 注入、HTTP MCP 和 API 测试：**23 passed in 4.85s**。

```text
.venv/bin/python -m pytest -q \
  tests/unit/memory_bank \
  tests/unit/application/test_memory_service.py \
  tests/unit/runtime/test_memory_tools.py \
  tests/integration/runtime/test_memory_injection.py \
  tests/integration/runtime/test_memory_mcp.py \
  tests/integration/api/test_memory_bank_api.py
```

另外通过独立内存 Repository 场景复现，没有写入业务数据库：

| 场景 | 实测 |
| --- | --- |
| 已存“用户偏爱简短回答”，搜索“请保持言简意赅” | 0 命中 |
| 两次提议并确认完全相同内容 | hash 相同，生成两个不同 entry |
| 较早的相关记忆后加入 20 条其他主题 | 关键词搜索能找到，运行时投影没有它 |
| 旧 UserMemory 长度为 4000 | 最终投影为 4000 字符，没有 Memory Bank 数据块 |
| 从 Service 提议 4200 字符，模拟 MCP 调用路径 | 能存入；投影截到 4000，结束标签丢失 |

以上说明现有测试保障基础治理，但尚未覆盖有效长期召回和上下文装配。未运行真实 PostgreSQL 集成测试、线上服务检查或 OpenViking 模型评测；本文没有声称任何模型召回率或成本提升已经得到验证。

## 4. OpenViking 的可借鉴实现

### 4.1 统一上下文地址与内容/索引分离

OpenViking 以 `viking://` 地址组织 memory、resource、skill、session，通过 VikingFS 提供目录和文件操作。正文使用 AGFS/RAGFS，语义索引另行维护，索引含 URI、向量和检索用标量/摘要；不是只存向量，也不能理解为索引完全不包含文本。

价值在于：记忆有稳定地址，能够浏览、按地址读取、追溯和重建索引。文件系统是逻辑接口，不要求本项目把所有 PostgreSQL 记录搬成磁盘 Markdown。

对本项目：可以给现有对象建立统一 ContextRef，底层继续使用 PostgreSQL、对象存储、KnowledgeSnapshot 和已发布 Skill Bundle。只读入口可统一，写入权限与发布生命周期保持各自规则。

来源：[存储架构](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/docs/en/concepts/05-storage.md)。

### 4.2 L0/L1/L2：目录摘要、概览和原文

该快照目录侧文件的默认正文限制为：L0 `.abstract.md` 256 字符，L1 `.overview.md` 4000 字符，L2 是原文或解析后内容。L0/L1 主要是目录级语义侧文件，不是每个普通文件都生成三份副本。目录层语义从子项向上生成。

对短小偏好，一条几十字的事实本身已经足够，不宜机械再生成两层摘要。分层更适用于项目主题、实体集合、历史案例、长文档和技能目录。

可采用：先返回主题概览和引用，再根据本轮任务加载原文；把来源、版本和适用条件保留下来，避免摘要丢掉例外条件。

来源：[上下文分层](https://docs.openviking.ai/en/concepts/03-context-layers)。

### 4.3 目录递归检索

OpenViking 的检索器包含全局向量定位、起始目录选择、优先队列递归、子节点评分与可选 rerank，并有收敛条件。`find()` 适合直接查询；带会话的 `search()` 支持意图分析与查询拆分。

这种结构适合“某项目—某业务主题—某事件或经验”的内容。它不是一定优于平面检索：目录归类错误、摘要遗漏、上层剪枝都可能漏召回；模型调用与递归层数也会增加延迟。

源码里存在父子分数传播参数，但当前文档默认 alpha=1.0，即不混入父分数。因此不能把有层次目录夸大为默认做了强路径推理。

对本项目的顺序：先证明授权范围内的混合语义召回有提升；目录检索可与叶子级召回并用，作为后续优化，而不是最初上线依赖。

来源：[检索概念](https://docs.openviking.ai/en/concepts/07-retrieval)、[HierarchicalRetriever 源码](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/openviking/retrieve/hierarchical_retriever.py)。

### 4.4 最值得立即借鉴：Context Assembler

当前 OpenViking 已有 `search(mode="context")`，不仅返回 Top-K，还做分类候选配额、预算装配、层次降级、跨轮去重和可选摘要重写。其预算代码先给候选分配基本展示层次，再利用剩余预算补充细节，单项过大时降级而非切断内容。

这与本项目问题高度匹配：避免第一条长记忆吃掉全部预算；不给相同信息每轮重复占满上下文；保留可进一步读取的 URI。

不能直接照着概念 API 接入：该快照 `mode="context"` 不支持 `target_uri`，`/search/recall` 已被标记为兼容入口；候选的 abstract 字段在部分记忆类型中可能携带完整短正文。应选择固定范围的 list/find 后在 Harness 装配，或在验证过的后端过滤契约下使用 context 模式，不能传一个未支持的路径参数假定隔离生效。

源码：[预算算法](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/openviking/retrieve/context_assembler/budget.py)、[HTTP 检索 Router](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/openviking/server/routers/search.py)、[检索 API](https://docs.openviking.ai/en/api/06-retrieval)。

### 4.5 自动提取与 V3 增量合并

源码中的 session commit 分为归档与后台处理：先保存可恢复的归档/提交状态、投递持久队列，再后台摘要和提取，使用 task_id 跟踪完成。收到 accepted 不能当作新记忆已检索可见。

当前核心是 `SessionCompressorV3`。用户记忆流程是上下文准备与范围处理 → ExtractLoop 产生结构化操作 → StreamingMemoryUpdater 批量合并 → MemoryUpdater 写回、索引和记录变化。

提取类型由 YAML schema 指定目录、文件命名、字段与 merge_op。例如 preference 的 user/topic 为 immutable，content 用 patch；这比把整段历史重新覆盖为一个 memory 文本更适合保持主题身份。

合并器有按用户的进程内注册表和按 peer/type 分组，默认按 8 个操作或约 10 秒窗口触发处理。可借鉴“LLM 提议变更，确定性组件验证并执行”的分工；这些批处理和路径锁不能直接替代本项目跨 Worker 的数据库 CAS 与任务幂等。

落地时优先实现结构化候选 `create / merge / supersede / ignore`，只把通过现有治理的变更发布为 active。OpenViking 当前普通提取主路径会写回，不能假定存在一个配置开关即可返回待审批候选。若复用其完整提取器，需隔离候选命名空间并禁止检索，或适配内部提取接口；首期自己实现候选提取通常更可控。

来源：[Session 实现](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/openviking/session/session.py#L1851)、[V3 提取](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/openviking/session/compressor_v3.py#L651)、[Streaming updater](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/openviking/session/memory/streaming_memory_updater.py)、[Preference schema](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/openviking/prompts/templates/memory/preferences.yaml)。

### 4.6 执行经验演进

OpenViking 支持 cases、trajectories、experiences 等类型；当前策略中启用 experiences 会同时激活相关演进类型。源码里的训练组件主要产出和更新经验/技能内容，不能等同于对底层大模型做参数微调。

这一方向适合本项目的领域 Agent。例如一次档案分类失败，可沉淀“文件名不足以分类时要结合正文”的带条件经验；一次材料撰写成功，可记录哪些来源检查和交付验证有效。

但当前 experiences 模板明确要求生成命令式执行指令。它不应直接进入本项目标记为 never instructions 的普通用户记忆块，更不应自动覆盖已发布技能。建议独立的经验候选 → 回放评测 → 版本化审核发布链路。

来源：[经验模板](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/openviking/prompts/templates/memory/experiences.yaml)、[Memory policy](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/openviking/session/memory_policy.py)、[Policy updater](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/openviking/session/train/components/policy_updater.py)。

## 5. 不能直接照搬的边界

### 5.1 身份范围不等价

本项目默认范围是 tenant/user/agent。OpenViking 主要以 account/user 隔离，peer 是用户内的内容范围。Actor peer 限制其他 peer，并不自动排除 self 空间或账户共享资源。

PoC 可保守映射：`account = tenant_id`，`user = opaque(harness_user_id, stable_agent_id)`；初期只写该用户的私有空间。这里 opaque 标识需要确定性生成并保存映射，不接受模型传入身份。代价是用户级共享需要后续明确授权，不能无意中通过 common root 实现。

如需真实 user + peer 映射，则代理必须固定 peer 与允许的 namespace/类型，对 search、read、list、摘要、rerank 输入和缓存一并约束。URI 前缀是寻址手段，不能独立当成访问控制。

来源：[多租户与 Peer 范围](https://docs.openviking.ai/en/concepts/11-multi-tenant)。

### 5.2 删除必须覆盖派生内容

OpenViking 的会话归档会包含原始消息，memory_diff 可包含 before/after/deleted_content。删除当前记忆文件，不意味着相关原文、差异、摘要、索引、缓存、快照和备份都消失。

对本项目最适合的初始接法是只同步已生效记忆，不复制全部聊天。建立 lineage，删除权威记录时同步撤销派生结果和摘要；索引延迟时先以权威状态阻止读取，再完成后台清理。关闭自动保存只影响未来写入，是否同时清理已有记忆应是独立操作语义。

来源：[Session memory diff](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/docs/en/concepts/08-session.md)。

### 5.3 运行和一致性成本

直接接入新增内容后端、向量索引、embedding、提取模型、可选 rerank、队列和后台摘要；不仅是一项数据库配置。读取可按需节省 prompt，但写入、合并和上层摘要刷新会消耗模型调用。

官方当前分层文档明确指出资源/技能的父摘要刷新仍有写放大优化项；多写存储文档也指出同一 primary 的多进程并发元数据锁仍有后续工作。副本多写不等于完整分布式多活保证，PoC 首期宜单实例、持久卷和可恢复队列，再以压测与恢复测试决定扩容方式。

来源：[分层 freshness 限制](https://docs.openviking.ai/en/concepts/03-context-layers)、[多写存储限制](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/docs/en/concepts/14-multi-write-storage.md)。

### 5.4 许可证与版本

该快照主项目 LICENSE、Python metadata 和核心源码标记为 **AGPL-3.0**；README 对 CLI、examples 等列出其他许可。本项目根 LICENSE 为 Apache-2.0。官方文档站页脚的 Apache-2.0 不能用来推断核心代码许可。

借鉴设计、自行实现与直接复制代码是不同路径。直接采用或修改核心实现前，应按具体使用和交付方式核查许可义务；独立 HTTP 服务是清晰的工程边界，但不能作为自动免除许可义务的结论。本文不作法律适用判断。

源码：[OpenViking LICENSE](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/LICENSE)、[组件许可说明](https://github.com/volcengine/OpenViking/blob/0cd847aa4fa4f2f3a4182d2f1068e063c4c991cf/README.md#license)。

文档与源码存在演进差异，例如部分 API 文档的类型启用列表与当前概念文档不完全一致，事务文档的旧 commit 描述也不完全对应当前 session.py。本文按固定源码判断行为，实际接入要做契约测试，不依赖旧教程中的六类记忆、旧路径或旧 recall 接口。

## 6. 三种实现路线

| 路线 | 收益 | 代价 | 建议 |
| --- | --- | --- | --- |
| A：在 Memory Bank 内借鉴实现 | 保留治理和运维体系，最快修复运行时召回 | 需实现语义后端、装配和提取任务 | 作为主路线 |
| B：Memory Bank + 独立 OpenViking 检索服务 | 复用层次检索与上下文引擎，可扩展到资源/技能 | 身份映射、投影同步、删除闭环、模型和服务运维 | 小范围 PoC，与 A 比较 |
| C：由 OpenViking 全量接管记忆/会话/知识/技能 | 统一系统边界 | 迁移、治理、快照和发布语义改造最大 | 当前阶段不建议 |

如果未来目标是大量项目文件、跨会话实体网络、经验库和技能联合召回，B 的收益更大；如果目标主要是记住偏好、项目事实和近期决策，A 更经济。

### 6.1 推荐组件边界

```mermaid
flowchart LR
    U[用户问题] --> C[Memory Context Service]
    C --> S[授权范围计算]
    S --> R[异步 Retrieval Backend]
    R --> P[Postgres 语义索引 或 OpenViking]
    P --> V[权威状态和版本复核]
    V --> A[预算装配和来源引用]
    A --> RT[Claude / Codex Runtime]
    RT --> E[可信执行事件和用户反馈]
    E --> X[后台候选提取与合并]
    X --> G[Memory Bank 治理]
    G --> DB[(Postgres 权威记录)]
    DB --> O[事务 Outbox]
    O --> P
    DB --> V
```

查询前范围过滤和查询后权威复核各有作用：前者防止数据进入错误用户的检索、摘要和重排过程；后者处理删除、过期与索引延迟。二者不能互相替代。后端若直接生成跨条目摘要，需提供来源版本清单，任一源失效时该摘要必须重建或拒用。

### 6.2 接口需要调整，不能只替换现有 Adapter

现有 `MemorySearchAdapter.search(entries, query)` 是同步内存函数，接收应用预先加载的 1000 条数据，不适合远程检索。

建议设计独立异步端口，参数为可信 scope、query、limit 和过滤条件，返回 entry_id/version/ref/score；内容优先按权威记录装配。MemoryContextService 负责常驻偏好、相关事实、预算、引用和 Runtime 统一接入。

建议修改点：

| 当前文件/入口 | 改动 |
| --- | --- |
| `memory_bank/models.py` | 新增 memory_type、stable agent scope、key/topic、适用条件、valid_from/valid_to、supersedes、source_refs |
| `memory_bank/search.py` | 保留确定性基线；增加异步后端契约，去除先取最新 1000 条限制 |
| `memory_bank/service.py` | 精确幂等、合并提议、状态复核、跨派生记录删除 |
| `application/memory.py` | 旧记录迁移，改为 query-aware Context Service，完整条目预算装配 |
| `worker/orchestrator.py` | 把本轮问题及有限会话摘要传入召回；执行后发布提取任务 |
| `runtime/memory_tools.py`、`memory_bank/workload.py` | 增加 scope-bound search/read，保留 propose；统一长度和限流 |
| `runtime/registry_codex_runtime.py` | 对齐受控 Memory MCP，复用同一 Context Service |
| PostgreSQL migrations / repository | 条件索引、幂等键、outbox、lineage、派生版本 |
| maintenance / lifecycle | 索引清理、摘要失效、失败重试和同步对账 |

以上是拟议改造清单，本次未实施。

### 6.3 读取与写入顺序

读取：可信身份 → allowed scopes → 常驻偏好 + query 相关候选 → scope/status/expiry/version 复核 → 去重和冲突处理 → 完整条目预算装配 → 来源引用 → Runtime。

写入：已持久化事件 → 幂等提取任务 → 事实候选 → 敏感/信任/证据校验 → 精确去重与合并提议 → 用户确认或明确策略 → CAS 与 outbox 同事务提交 → 派生索引与摘要刷新。

初期 query 用用户问题和有限恢复摘要即可；复杂意图分析是可选项。长会话可按已处理 event sequence 划分批次，幂等键包含 session、事件范围和 extractor version，防止失败重试重复写入。

“更近”只应用于相同事实键和相同条件下的更新。例如“日常回答简短”和“正式报告展开论证”不冲突，不能被最新事实统一覆盖。用户明确纠正比模型猜测更有证据权重；冲突无法判定时保留候选与待确认状态。

### 6.4 索引作为可重建投影

若选 OpenViking，先同步 active、未过期条目，以 entry_id/version 生成稳定映射。不要由 PostgreSQL 和 OpenViking 同时自行决定事实合并，否则会形成两个权威来源。

同步任务使用单调版本和 tombstone，旧的新增消息不得覆盖较新的删除；失败重试应可重复执行。查询回源复核使用的版本必须与摘要来源一致。删除操作优先使在线召回不可见，后台再完成所有派生清理；任务有可观测状态和补偿对账。

## 7. 分阶段交付与验收

### P0：先修复现有路径

交付：运行时按问题召回；完整条目预算；旧文本迁移/隔离预算；统一内容上限；精确去重；search/read 工具；Claude/Codex 路径对齐；权限范围使用稳定 Agent 标识。

验收：本次五个复现用例转为回归；超过 20 条后旧事实能被用到；数据块始终完整；删除和过期不返回；同名不同 owner 不会意外共用记忆。

### P1：同一数据集比较检索后端

固定同一批已确认事实、同一权限、相同候选数和注入预算，比较关键词基线、数据库语义检索、OpenViking。检索收益评测尽量控制 embedding 和 rerank 变量；如果使用不同模型，必须说明比较的是整套系统而非算法。

建议数据集包含 100–200 个业务问例：中文同义表达、时间纠正、项目消歧、旧事实、无相关记忆、跨租户/用户/Agent、删除/过期、长条目和多轮复用。规模是建议，不是现有评测数据。

指标：Recall@K、Precision@K、无关召回率、正确应用记忆的回答比例、冲突事实采用率、每轮 token、检索 p50/p95、写入模型成本、索引滞后、删除不可见时间。隔离和删除违规必须为零；效果和延迟阈值根据基线及业务 SLO 决定。

先做 shadow 召回，只记录差异，不改变真实回答。后端超时降级到授权有效的基础召回；诊断区分“没有相关记忆”与“检索后端失败”。

### P2：候选提取和事实更新

交付：任务结束或明确会话边界后异步提取偏好、项目事实、实体与决策；展示候选、合并关系、来源片段；用户或既有策略决定生效。首期不从混合不可信网页的整段对话无差别提取，按来源片段保留信任标记。

验收：失败重试无重复；用户纠正可替代旧事实；条件不同的偏好共存；来源不足不自动激活；删除后不会由未处理的旧事件重新生成。

### P3：经验与资源联合检索

在真实业务指标改善后，增加项目目录概览、案例和经验集合、资源和技能联合导航。程序性经验作为候选规则，经回放评测与版本发布后使用；Skill Bundle 的不可变发布语义保持有效。

## 8. 建议的首次业务演示

1. 在会话 A 明确记录“正式材料使用中文，结论在前，附原始来源”，经确认生效。
2. 新增 30 条不相关偏好，确保关键记忆不在最近 20 条中。
3. 会话 B 用不同措辞请求一份材料，系统召回对应偏好并显示来源引用。
4. 会话 C 补充“内部简报控制在一页”，形成有条件的项目/文种偏好，不覆盖全部写作风格。
5. 删除其中一条，再发起同样请求，确认正文、摘要和索引都不会继续提供该记忆。
6. 换用户、换 Agent owner、换租户分别验证无法看到这条记忆。

这组演示能直接证明长期记忆的产品价值和治理边界，也能作为是否引入 OpenViking 的实际决策依据。
