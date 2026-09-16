# Tavily MCP 平台级退役 · 代码修复记录

- 日期：2026-09-14
- 分支：`feature/weknora-knowledge-base`（工作区未提交改动）
- 起因：173 用户报错 `Agent draft is not ready: MCP 能力已禁用：tavily-readonly`
- 结论：平台整体退役 `tavily-readonly` MCP。目录、草稿、运行时三层在**加载时自动剥离**，
  不依赖数据库清理即可自愈；数据库物理清理为可选的收敛步骤（清单见 §5）。

## 1. 根因

- 173 `local` 租户目录里 platform 的 `tavily-readonly` 被禁用（`capability_catalogs.payload`
  中 `enabled=false`），而 4 条草稿的 `spec.mcpServers` 仍引用它。
  `compiler.py` 对 `enabled=false` 的引用抛 `mcp_server_disabled`（即用户看到的报错）。
- 代码层 `default_capability_catalog()`（`catalog.py`）始终内置 tavily，且
  `default_tools.py` 的运行时基础注册表硬编码了 tavily 注册项——单删 DB 条目会在下一次
  `CapabilityCatalogService.get()` 时被 `_append_missing` 复活。因此必须改代码。

## 2. 173 数据现状（2026-09-14 排查，部署分支发布 `deepagents-export-20260914` 之前）

| 位置 | 内容 |
| --- | --- |
| `capability_catalogs` | `local`：platform tavily（disabled）+ user_1c16a899 的个人副本（disabled）；`tenant-a`：platform tavily（enabled）；`deployment-smoke-6a08091`：platform tavily（enabled） |
| `agent_drafts` | 4 条引用：`draft_d146dd08…`（smoke）、`draft_acc3136c…`（agent-e2850a8254，rev4）、`draft_bbf92d8e…`（networked-knowledge-research-agent，rev184，另有 knowledge-search）、`draft_84591f91…`（parenting-expert，rev8）；全部 eager 模式 |
| `agent_versions` | 9 条已发布/校验版本在编译产物 `manifest.spec.tools` 里绑定 tavily 工具（public-opinion-agent 0.3.5×2/0.3.7、parenting-expert 0.1.1、agent-e05f29865f 0.1.0、agent-e2850a8254 0.1.0、4 条 validated preview）；`tool_directory.entries` 同样包含 |
| `mcp_credentials` | 1 条：`local`/user_1c16a899/`tavily-readonly` |
| `.env.production` | `HARNESS_MCP_SECRET_REFERENCES_JSON` 与 `HARNESS_MCP_SERVER_SECRETS_JSON` 含 tavily 映射（值为占位符） |

## 3. 代码改动（自愈三层）

**目录层**（部署后首次 `get()` 即剥离并持久化，revision +1）：
- `catalog.py`：默认目录 `mcpServers=()`；四个执行档案 `allowedMcpReferences=()`；
  新增 `RETIRED_PLATFORM_MCP_REFERENCES = frozenset({"tavily-readonly"})`。
- `catalog_service.py`：新增 `_retire_platform_mcp_servers()`，在 `get()` 升级链末尾剥离
  所有 retired 引用的 MCP 条目（含个人副本）并清理执行档案的授权列表；
  `_EDITABLE_PLATFORM_MCP_REFERENCES` 清空；删除 legacy tavily query-auth 迁移块。

**草稿层**：
- `storage/studio_repository.py`：`_load_draft` 剥离 `spec.mcpServers` 中的 retired 引用
  （加载即净化，保存时落库）；`_summary_fields` 的 toolCount 同步排除。

**运行时层**（已发布版本的编译产物仍含 tavily 工具钉扎，不再阻断执行）：
- `runtime/tools.py`：`resolve()` 跳过 retired 引用（不再 `ToolResolutionError`），
  且计算「是否需要租户注册表」时排除 retired 引用（避免无谓的目录查询）；
  `web_server()` 删除「拿 tavily-readonly 凭据当 WebSearch key」的回退。
- `runtime/default_tools.py`：删除硬编码 tavily 注册与 smoke 特判，基础注册表清空。
- `registry_codex_runtime.py`：`_OPTIONAL_CODEX_MCP_SERVERS` 清空（tavily 不再豁免 required）。
- `composition.py`：read-only MCP 集合不再并入 `TAVILY_REFERENCE`。
- `web_configuration.py`：`client()` 删除 tavily-readonly 凭据回退。
- `policy/rules.py`、`policy/profiles.py`：删除 tavily 工具 ALLOW 规则。
- `deployments/models.py`：`EnvironmentResourcePolicy.allowedMcpReferences` 默认 `()`。
- `agent_builder.py`：推荐语去掉 Tavily 字样。
- `deploy/helm/agent-harness/values.yaml`：egress 白名单移除 `.mcp.tavily.com`。
- 前端 `agent-studio.ts`：`MCP_OPTIONS` 兜底清空（目录数据为唯一来源）；
  `mcp-catalog-control-plane.tsx` 平台可编辑集合清空；通用 Lead 智能体的 MCP 白名单
  `GENERAL_LEAD_MCP_REFERENCES` 清空 —— 保留「Lead 不获得业务 MCP/知识库」的产品规则，
  平台已无通用 MCP，故 Lead 现在不出现任何 MCP 选项。
- 前端死代码清理：`agent-studio-workbench.tsx` 的 `false && …tavily-readonly` 提示块、
  `activity-summary.tsx` 联网工具统计里的 `includes("tavily")`、
  `governance-control-plane.tsx` 的 `personal-tavily` / `settings://mcp/tavily` 占位符。
- `deploy/docker-compose/.env.docker.example`：平台 MCP 凭据示例改为空对象。

**边界（未改，属平台联网能力而非 MCP）**：`web_tools.py`/`config.py` 的 WebSearch
provider（tavily REST / minimax）、个人设置「联网搜索服务」选项。个人/服务端 key 照常生效；
都没有时报既有错误「平台尚未配置搜索服务凭据；可先使用网页读取」。

## 4. 部署要求（下一构建）

1. **api、web、worker×3、quality-sync 必须同批滚到同一 tag**。worker 仍在
   `route-guard-20260910-r2`：旧代码的 `_upgrade_system_managed_catalog` 会把 tavily
   重新补回 system 类目录，且旧运行时不认识 retired 跳过语义。
2. 无 alembic 迁移（纯加载时净化）。
3. 部署后验证：
   - `GET /v1/studio/capabilities` 的 `mcpServers` 不含 tavily（各租户）；
   - 原 4 条草稿 validate/publish 通过；
   - parenting-expert 0.1.1 等已发布版本可正常开会话（tavily 工具静默消失）。

## 5. 173 物理清理清单（可选收敛；不清理也自愈）

```bash
# 备份（按 model-route-20260913 修复流程）
mkdir -m 700 -p /data/agent-studio-repairs/tavily-retirement-20260914
docker exec agent-studio-173-postgres-1 pg_dump -U harness -d harness \
  -t capability_catalogs -t agent_drafts -t agent_versions -t mcp_credentials \
  > /data/agent-studio-repairs/tavily-retirement-20260914/before.sql
```

- `capability_catalogs`：三个租户 payload 里的 tavily 条目与执行档案授权
  （api 起来后首次 `get()` 也会自动落库剥离，此步可省）；
- `agent_drafts`：4 条草稿的 `spec.mcpServers`（同上，加载时净化，保存后落库）；
- `agent_versions`：9 条快照的 tavily 工具项（运行时已跳过；如需彻底清理需按
  `ToolDirectorySnapshot.create` 重算 `toolDirectory.content_hash` 与快照
  `content_hash`，连同行内 `manifest_hash` 一起更新，事务内进行）；
- `mcp_credentials`：删除 user_1c16a899 的 `tavily-readonly` 行；
- `.env.production`：`HARNESS_MCP_SECRET_REFERENCES_JSON` /
  `HARNESS_MCP_SERVER_SECRETS_JSON` 移除 tavily 映射（下一 tag 部署时顺带）。

## 6. 测试基线

- 后端：`tests/unit` 1076 通过；`tests/integration/api` 119 通过；`ruff check` 通过。
  新增/改写用例：目录退役剥离（catalog_service）、草稿引用剥离、retired 引用运行时跳过
  （default_tools/production_composition/runtime_composition）、retired 平台 MCP 不可删
  （agent_studio_api）等；删除过时的 `test_tavily_mcp_live.py` 与
  `tests/fixtures/agents/tavily-live-agent`；样例 `networked-knowledge-research-agent`
  去除 tavily（manifest/prompt/eval/skill，eval 用例 5→4）。
- 前端：`tsc --noEmit` 通过；`vitest run` 587 通过（改写 3 个断言：目录可编辑集合字面量、
  MCP 兜底为空、Lead 不再获得 tavily）。
- 既有失败（与本次无关，已用 stash 在 HEAD 复现）：后端
  `test_migration_0028`、`test_final_readiness`、`test_lifecycle` 与
  `test_registry_runtime` 的 glm-5-2 用例、`test_agent_bundle_api` 1 项；
  前端 `studio-client.spec.ts` 1 项、`workbench-layout.spec.ts` 2 项。

## 7. 真实数据端到端验证（2026-09-14，173 数据 + 本分支代码）

把 173 `local` 租户的真实 `capability_catalogs.payload`（revision 72）与 4 条引用 tavily
的 `agent_drafts` 导出到本地，直接跑本分支的代码路径（`CapabilityCatalogService.get`、
`storage.studio_repository._load_draft`、`AgentDraftCompiler`）：

| 阶段 | 结果 |
| --- | --- |
| 修复前（真实目录 + 真实草稿） | `Agent draft is not ready: MCP 能力已禁用：tavily-readonly` —— 与用户报错逐字一致，根因确认 |
| 目录退役迁移 | revision 72 → 73；`mcpServers` 只剩 `sentiment_query_mcp`（tavily 的 platform 条目与个人副本均被剥离）；四个执行档案的 tavily 授权清空 |
| 草稿加载净化 | 4 条草稿的 `spec.mcpServers` 全部剥离 tavily |
| 编译校验 | tavily 相关 issue 0 条；`office-builder-smoke`、`parenting-expert` 从「不可编译」变为 `ready=True` |

### 7.1 顺带发现的两条既有问题（与 tavily 无关，需单独处理）

1. `draft_bbf92d8ed3e94ad7b3897192221448bf`（`networked-knowledge-research-agent`，
   owner `developer`，rev 184）引用 MCP `knowledge-search`，但该引用在 173 **任何**
   `capability_catalogs` 里都不存在（全库检索 `knowledge-search` 命中 0 次）。
   当前代码里知识 MCP 的注册名是 `harness-knowledge`（`knowledge/workload.py`），
   因此这是历史命名的遗留引用：该草稿在本次修复前后都编译不过，需重新注册该 MCP
   或从草稿移除该引用。173 上另有 3 个知识库（`weknora-173-verify`、`hehe`、`policy-wiki`），
   但草稿的 `knowledgeReferences` 为空。
2. `draft_acc3136c3afe4d0881d203f753630b08`（`agent-e2850a8254`，rev 4）运行时为
   codex-app-server，而模型路由协议是 `anthropic_compatible`，报
   「Codex App Server 不支持模型路由协议 anthropic_compatible」。同样是既有配置问题。

### 7.2 线上状态

当前 173 跑的是 `deepagents-export-20260914`（已确认镜像内 `catalog.py` 无退役常量、
`default_tools.py` 仍含 3 处 tavily），worker×3 与 quality-sync 仍是
`route-guard-20260910-r2`。本次退役代码**尚未上到 173**，随下一 tag 按 §4 发布后，
`parenting-expert`、`office-builder-smoke` 类草稿的错误即消除。

## 8. 174 环境分析（2026-09-14）

结论：174 是「只做数据清理、不修代码」的现成反例 —— 有人已经删过 tavily，但清理不完整且
破坏了别人的草稿。

### 8.1 环境状态

| 项 | 174 实测 |
| --- | --- |
| api / worker×3 / quality-sync | `kai/axis-api:20260908-recovery-webconfig2`（09-08 构建，**早于**本修复） |
| web | `agent-studio-web:composer-compact-20260813-c68d8c5`（08-13，比 api 还旧）；另有 `axis-web-20260908-recovery-webconfig3` 在跑 |
| alembic | `0031`（173 是 0032） |
| 数据量 | 14 草稿 / 84 版本 / 642 runs（近 14 天 104，活跃 0）/ deployments 0 / 知识库 0 |
| 联网搜索 | provider = **minimax**，key 已配；**未配置** `HARNESS_MCP_SECRET_REFERENCES_JSON`（为空 `{}`） |
| tavily 凭据行 | 无 |

### 8.2 tavily 残留分布

| 位置 | 状态 |
| --- | --- |
| `capability_catalogs` / `local`（rev 36，用户编辑） | MCP 条目**已被删除**（2026-09-08 12:37:57 `DELETE /v1/studio/catalog/mcp/tavily-readonly/permanent`，audit：`user_1c16a899…`，success）；但**四个执行档案仍授权** `tavily-readonly` |
| `capability_catalogs` / `tenant-a`（rev 2，`system:model-control-plane-import`） | tavily **enabled**，platform 所有 |
| `agent_drafts` | 2 条（owner `developer`）：`public-opinion-agent` rev 3（08-02）、`networked-knowledge-research-agent` rev 110（08-17，另引用 `knowledge-search`） |
| `agent_versions` | 3 条已发布版本钉着 tavily 工具（public-opinion-agent 0.3.5 ×2、0.3.7），运行时均为 `claude-agent-sdk` |

### 8.3 关键发现：删除的引用守卫是「按人」的，所以删了也白删

`CapabilityCatalogService.impact()` 对 `mcp` 用的是 `list_for_user(tenant_id, user_id)`——
只统计**发起删除的用户自己**的草稿（已在 174 部署镜像内 `inspect.getsource` 确认）。
本次删除由 `user_1c16a899…` 发起，而两条引用草稿属于 `developer`，于是守卫判定「无引用」，
删除返回 200，tavily 从目录消失，但别的用户的草稿仍绑着它 —— 两条草稿随即从可编译变成
`MCP 能力未注册：tavily-readonly`（**未注册**形态，区别于 173 的**已禁用**形态，已用 174
真实 payload 在修复前代码上复现）。

这是「只清数据不修代码」会踩的坑：清理方看到的是成功，破坏面在别人的草稿里，而且
即便这次删对了，下一批 `system` 类目录（新租户首次 `get()` 即 `updated_by="system"`）仍会把
tavily 从代码默认目录里补回来。

### 8.4 影响面（实测，非推断）

- 那 3 条钉 tavily 的已发布版本是 **claude-agent-sdk** 运行时：接口内实测 `resolve()` 在 174
  配置下严格模式抛 `McpCredentialError: missing MCP credentials: tavily-readonly.api_key`；
  该运行时以 `tolerate_unavailable_mcp=True` 调用，因此实际表现为 `unavailable_mcp =
  {tavily-readonly: (tavily_search, tavily_extract)}` 的**降级运行**（工具静默消失），不是失败。
- codex 运行时不含该容错（`registry_codex_runtime.py` 直接调 `resolve()`）。174 上目前没有
  codex 版本钉 tavily，所以没有硬失败。
- 结论：174 当前是**草稿编辑阻塞**，不是运行阻塞；且与 173 不同，173 的目录条目是「已禁用」，
  174 是「已删除」，两者报错文案不同但同一根因。
- 174 的内建联网搜索不受影响（provider=minimax + 已配 key，原本就不走 tavily 凭据回退）。

### 8.5 本修复对 174 的效果（真实数据实测）

用 174 真实 `local` 目录 + 2 条草稿跑本分支代码：

| 阶段 | 结果 |
| --- | --- |
| 修复前 | `MCP 能力未注册：tavily-readonly`（两条草稿） |
| 目录迁移 | rev 36 → 37；四个执行档案的 tavily 授权清空 |
| 草稿净化 | 两条草稿剥掉 tavily；`public-opinion-agent` 变为 `ready=True` |
| tavily 残留 issue | 0 |
| 遗留（与 tavily 无关） | `networked-knowledge-research-agent` 仍报 `MCP 能力未注册：knowledge-search`；174 知识库为 0，该引用是无主的 |

### 8.6 174 建议动作

1. 与 173 同批发布本修复（api + web + worker×3 + quality-sync 同 tag）；顺带把 174 的
   web 镜像与 api 对齐（现在是 08-13 对 09-08 的错配）。
2. 若不等发布：从那 2 条草稿移除 `tavily-readonly` 即可消除报错（纯数据编辑，立即生效）；
   顺带清空四个执行档案的 `allowedMcpReferences`（当前线上只剩这两处会咬人）。
3. `tenant-a` 的 enabled 条目：无人引用，可删可留；本修复上线后会自动消失
   （该租户 `updated_by` 是 `system:` 前缀，不触发 `_append_missing` 复活）。
4. `knowledge-search` 无主引用在 173/174 都存在，属另一项清理（注册知识 MCP 或移除引用）。
5. **独立缺陷（建议单独修）**：`impact()` 对 MCP 的引用守卫按用户隔离，导致 A 用户可以删除
   B 用户草稿仍在使用的 MCP。本次 tavily 退役绕开了这个坑，但它对用户自注册 MCP 依然成立
   （`delete_mcp` 的 `ConflictError` 守卫同样只看得见自己的草稿）。

## 9. 173 数据清理执行记录（2026-09-14，已实施）

在不部署新镜像的前提下，直接在 173 数据库上完成 tavily 清理，使草稿编译错误立即消失。

### 9.1 备份（可回滚）

`/data/agent-studio-repairs/tavily-retirement-20260914/`（`umask 077`，root 只读）：

| 文件 | 内容 |
| --- | --- |
| `before.sql` | `capability_catalogs` + `agent_drafts` 的 pg_dump 全量（1.6 MB） |
| `rollback.sql` | 由清理前实际值生成的定向回滚脚本（UPDATE 原 payload / revision / updated_by / updated_at，128 KB） |

回滚方式：`psql -U harness -d harness -v ON_ERROR_STOP=1 -f rollback.sql`。

### 9.2 执行内容（单事务 + 事务内自检，任一断言失败即整体回滚）

| 对象 | 改动 |
| --- | --- |
| `agent_drafts` × 4 | 从 `spec.mcpServers` 移除 `tavily-readonly`（`office-builder-smoke`、`agent-e2850a8254`、`networked-knowledge-research-agent`、`parenting-expert`）；**未动** revision / updatedAt / name，草稿 envelope 保持自洽 |
| `capability_catalogs` × 3 | 剥离四个执行档案的 `tavily-readonly` 授权、移除 tavily MCP 条目；`revision` 与 `payload.revision` 同步 +1（`local` 72→73、`tenant-a` 5→6、`deployment-smoke` 2→3）——加载路径要求二者一致，否则报 Corrupt Capability Catalog envelope |

事务内自检结果：草稿引用 0、目录条目 0、档案授权 0、envelope 不一致 0。

### 9.3 事后验证（用**线上修复前代码**跑清理后的真实数据）

| 检查 | 结果 |
| --- | --- |
| 4 条草稿的 `mcpServers` | 已无 tavily（修复前代码无剥离逻辑，说明是数据库改动本身生效） |
| `parenting-expert` / `office-builder-smoke` | `ready=True` —— 原报错消除 |
| tavily 相关校验 issue | 0 |
| `local` / `tenant-a` 目录 `get()` | 不再 bump revision、不再复活 tavily；envelope 校验通过 |
| `deployment-smoke-6a08091` 目录 `get()` | **会把 tavily 条目补回来**（`updated_by=system-route-migration` 命中 `startswith("system-")` 分支，`_append_missing` 重加平台默认；已实测 rev 3→4 且 mcp 回到 tavily）。授权不会被补回。该租户等代码修复上线后彻底消失 |

### 9.4 刻意保留 / 尚未处理

1. **`mcp_credentials` 的 tavily 凭据行保留**。173 api 容器未注入 `HARNESS_WEB_SEARCH_*`，`web_search_provider` 取代码默认 `tavily` 且无服务端 key；线上代码的内建 WebSearch 会回退读取该用户私有 tavily 凭据。删掉它会让该用户的联网搜索报「平台尚未配置搜索服务凭据」。代码修复上线后该回退被移除，届时凭据行变为惰性数据。**附带建议：173 的联网搜索配置本身是脆的**（provider 走默认值 + 无服务端 key，实际只对持有个人 tavily 凭据的那个用户可用）。
2. **9 条已发布/校验版本仍钉着 tavily 工具**（不可变快照，2 条 claude + 7 条 codex）。运行时走代码内服务端注册表，与本次目录改动无关。用真实凭据链在 173 容器内实测：

   | 用户 | 严格 resolve | 结果 |
   | --- | --- | --- |
   | `user_1c16a899…`（有个人 tavily 凭据） | OK | 解析出 tavily MCP |
   | `user_44229d566…`（无凭据） | FAIL | `McpCredentialError: missing MCP credentials: tavily-readonly.api_key` |

   codex 运行时（`registry_codex_runtime.py` 的 `execute`）**不容错**，因此 173 上 `agent-e2850a8254` v0.1.0（codex，owner `user_44229…`）在会话启动时会因缺凭据失败；claude 运行时容错，表现为降级。这是**既有**运行时风险，本清理既未引入也未消除；代码修复上线后这些工具被静默跳过，该风险随之消失。
3. `knowledge-search` 无主引用（173 与 174 皆然）与 `agent-e2850a8254` 的 codex/anthropic_compatible 路由不匹配，均为与 tavily 无关的独立问题。

## 10. 173/174 联网搜索可用性差异（2026-09-14 追加核查）

起因：用户在 173 上 WebSearch / WebFetch 可用，在 174 上「在智能体里勾选了也不行」，并要求
默认先用 MiniMax。

### 10.1 结论：174 的账号级「允许公开联网」开关是关的

用 owner 账号登录两侧控制台读取 `GET /v1/studio/web-configuration`（同一账号
`user_1c16a899…`，`local` 租户）：

| 字段 | 173 | 174 | 含义 |
| --- | --- | --- | --- |
| `enabled` | true | **false** | 个人「允许公开联网」开关 |
| `effectiveEnabled` | true | **false** | 平台开关 ∩ 个人开关，运行时按它决定是否注册工具 |
| `platformEnabled` | true | true | 平台 `HARNESS_WEB_TOOLS_ENABLED` |
| `provider` | minimax | platform | 个人选择的搜索服务 |
| `platformProvider` | tavily | minimax | 平台默认搜索服务 |
| `personalKeyConfigured` | true | true | 个人密钥已保存 |

- **174**：草稿「公文写作」（`govdoc-writer-agent` rev 5，`updatedAt` 2026-09-14T10:11、
  `networkToolsEnabled=true`、`runtime=claude-agent-sdk`、`builtinTools` 含 WebSearch/WebFetch、
  校验 issue 0）声明完全正确，但 `effectiveEnabled=false` 让 `ToolResolver.resolve()`
  在注册阶段静默跳过这两个 builtin（`runtime/tools.py:214` 的 `web_allowed` 判定）——
  运行里既不报错也不出现工具，表现为「勾了没用」。
- **173**：同一账号开关为开，且个人 `provider=minimax` + 个人密钥 → 可用。

### 10.2 173 的可用性来自个人密钥，不是平台密钥（修正 §9.4.1）

在 173 api 容器内用**线上镜像源码**核对：

- `WebConfigurationService.client()` 的线上源码**没有** tavily-readonly 凭据回退，只有
  `key = self._api_key` 一层；`Settings()` 实测 `web_search_provider='tavily'`、平台 key 为空。
  即 173 的平台默认搜索服务是「tavily + 无凭据」。
- 该账号能用，是因为它把个人 provider 固定为 `minimax` 并保存了个人 MiniMax 密钥。
- 推论：173 上把搜索服务留在「平台默认」且没有个人密钥的账号，联网搜索会报
  「平台尚未配置搜索服务凭据」；WebFetch 读取公开网页不需要密钥，仍然可用。

### 10.3 代码改动：平台默认搜索服务改为 minimax

`web_search_provider` 默认值 `tavily` → `minimax`（`config.py`、`runtime/tools.py`、
`runtime/default_tools.py`、`runtime/web_tools.py::PublicWebClient`、
`studio/web_configuration.py::WebConfigurationService`），并加两条钉住默认值的用例
（`tests/unit/test_config.py`、`tests/unit/studio/test_web_configuration.py`）。

注意：改默认值只决定「平台默认」解析到哪个服务，**不会**凭空产生凭据。要让 173 的平台默认
真正可用，仍需注入 `HARNESS_WEB_SEARCH_PROVIDER=minimax` 与 `HARNESS_WEB_SEARCH_API_KEY`
（174 已在 09-08 发布时按 `network.env` 配好 provider=minimax）。

### 10.4 待办与体验缺陷

1. **174 立刻可用**：个人设置 → 配置 → 打开「允许公开联网」（无需重新发布智能体）。
   本次只做核查，未代改账号设置。
2. **173 建议**按 §10.3 注入平台搜索服务凭据，替掉「默认 tavily + 无凭据」的脆弱配置。
3. **草稿勾选 ≠ 已发布版本**：若运行的是旧发布版本，还需确认该版本快照里也带这两个工具。
4. **体验缺陷（建议单独修）**：个人开关关闭时工具在运行时被静默剥离，运行与前端都没有
   「联网已被个人设置关闭」的任何提示，用户只能看到工具消失。
5. 174 的 `platformProvider=minimax` 已实测；但**平台 key 是否真的注入**在无 SSH 的 174 上
   无法直接取证（账号自身有个人密钥会掩盖 `credentialConfigured` 的来源），按 09-08
   发布记录 `network.env` 应已配置。

