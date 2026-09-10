# 混合/ Wiki 知识库配置项补全（新建向导）· 173 部署与验证记录

- 日期：2026-09-10
- 分支：`feature/weknora-knowledge-base`
- 目标环境：`172.20.109.173`（API `:8800`，Web `:3301`）
- 发布 tag：`kb-config-20260910`（api / worker ×3 / web 同 tag）
- 前置：本 tag 的 api 基座为 `lead-platform-skills-20260910-r3`，web 基座为 `citation-doc-20260910-r3`（引用文档抽屉）
- 起因：新建向导此前只有「类型 + 标识 + 名称 + 描述」，而 WeKnora 对开启 Wiki 的库还支持分块、Wiki 合成等配置（参考 WeKnora 控制台「索引策略 / Wiki 设置」）。本轮回填这些配置项。

## 1. WeKnora 侧真实配置契约（实测）

`GET /api/v1/knowledge-bases/{id}` 返回的配置字段（从 WeKnora 前端包与实例响应双向核对）：

| 字段 | 结构 | 含义 |
| --- | --- | --- |
| `indexing_strategy` | `{vector_enabled, keyword_enabled, wiki_enabled, graph_enabled}` | 索引策略开关，决定 rag / wiki / hybrid |
| `chunking_config` | `{chunk_size, chunk_overlap, separators}` | RAG 切片大小与重叠 |
| `wiki_config` | `{synthesis_model_id, max_pages_per_ingest, extraction_granularity, content_instructions, extraction_instructions}` | Wiki 合成模型、单次最大页面数、提取粒度（`focused`/`standard`/`exhaustive`）、内容生成要求、提取重点 |

## 2. 交付内容

**后端（配置从请求透传到 WeKnora）**

| 位置 | 改动 |
| --- | --- |
| `knowledge/models.py` | 新增 `KnowledgeBaseConfig`（`chunkSize` / `chunkOverlap` / `wikiGranularity` / `wikiContentInstructions` / `wikiExtractionInstructions` / `wikiMaxPagesPerIngest`，全部可空，带范围与 4000 字限制）与 `KnowledgeBaseGranularity` 枚举；`CreateKnowledgeBaseRequest.config` 接收它，并以 `engine_config()` 投影为引擎无关结构。 |
| `knowledge/ports.py` | 新增 `EngineBaseConfig`（引擎无关的创建期配置），`KnowledgeEnginePort.create_base` 增加 `config` 参数。 |
| `knowledge/service.py` | `create_base` → `_engine_create_base(..., config=request.engine_config())`。 |
| `weknora/gateway.py` | `wiki_config()` 合并操作员选择（粒度 / 内容要求 / 提取重点 / 最大页面数），`chunking_config()` 生成切片配置；未设置就不下发该键。 |
| `weknora/client.py` | `create_knowledge_base` 增加 `chunking_config` 参数。 |

关键语义：**留空 = 不下发该键 = 沿用平台默认**（如 `wiki_max_pages_per_ingest` 仍由 `HARNESS_WEKNORA_WIKI_MAX_PAGES` 决定），因此不带配置创建的库与历史行为完全一致。

**前端（新建向导）**

| 位置 | 改动 |
| --- | --- |
| `components/knowledge/knowledge-console.tsx` | 按类型展示配置分组：RAG / 混合显示「分块设置」（分块大小、分块重叠）；Wiki / 混合显示「Wiki 设置」（提取粒度三段选择含各自说明、Wiki 内容生成要求带 0/4000 计数、Wiki 提取重点、单次最大页面数）。只发送当前类型用到的分组，提交后随标识/名称/描述一起重置。 |
| `components/knowledge/knowledge-console.module.css` | 新增 `configGroup` / `configLegend` / `configHint` / `configCount` / `configRow` / `segmented*` 样式，沿用黑色主题；窄屏两列改单列。 |
| `lib/studio-client.ts` | 新增 `StudioKnowledgeBaseConfig` 与 `WikiGranularity` 类型，`createKnowledgeBase` 接受 `config`。 |

## 3. 测试与静态检查

- 后端：`tests/unit/knowledge` 56 项通过。新增 3 项——`test_gateway_hybrid_base_passes_operator_config_through`（chunking + wiki 全字段下发）、`test_gateway_hybrid_base_keeps_platform_defaults_when_config_is_empty`（空配置不下发任何键）、`test_create_base_forwards_wizard_config_to_the_engine`（请求 → 引擎结构，含首尾空格裁剪）；`FakeEngine.create_base` 同步新签名。
- 后端其余：`tests/unit/studio` + `tests/contract` + `tests/contracts` 201 项通过；`tests/contract/test_sdk_session_store.py` 因本机无 PostgreSQL（`127.0.0.1:5432` 拒绝连接）失败，属环境依赖，与本改动无关。
- 前端：`tests/knowledge-create-wizard.spec.tsx` 3 项通过（混合库下发全部分组且只下发相关分组、RAG 库只显示分块且留空不下发、Wiki 库只显示 Wiki 分组且默认标准粒度）。全量 89 文件 585 项：582 通过，3 项失败为改动前既有失败（`studio-client.spec.ts` 工作区草稿合并、`workbench-layout.spec.ts` 两项源码断言）。
- `tsc --noEmit` 通过；`ruff check` 通过；Next.js amd64 生产构建通过。

## 4. 部署步骤（可复现）

```bash
TAG=kb-config-20260910
# 1) API 增量镜像：只覆盖应用代码，不解析任何依赖（新增 api-code-only.Dockerfile）
scp src/harness 打包 + deploy/docker/api-code-only.Dockerfile → 173:/data/agent-studio-builds/$TAG/
docker build --build-arg BASE_IMAGE=harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:lead-platform-skills-20260910-r3 \
  -f api-code-only.Dockerfile -t harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:$TAG .
docker push .../agent-studio-api:$TAG
# 2) Web 镜像：本机交叉构建 amd64（运行时复用线上 web 镜像）
docker buildx build --builder agent-deploy-http --platform linux/amd64 --provenance=false \
  --build-arg RUNTIME_BASE=harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:citation-doc-20260910-r3 \
  -f deploy/docker/web-runtime-reuse.Dockerfile \
  -t harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:$TAG --push .
# 3) 发布（active-run 守卫 + env 备份 + 失败回滚；重建 api / worker ×3 / web）
ssh 173 'bash /data/agent-studio-builds/'$TAG'/deploy.sh'
```

无数据库迁移，`alembic_version` 保持 0032。`quality-sync-1` 不在本发布范围（它运行 `agent-studio-api:upstream-skills-20260910`，未重启）。

## 5. 173 实测结论

| 验证项 | 方式 | 结果 |
| --- | --- | --- |
| 服务健康 | `:3301` HTTP 200、`:8800/healthz` 200；api / worker×3 / web 容器 healthy | 通过 |
| 代码就位 | api 容器内 `harness/knowledge/**` 含 `EngineBaseConfig`；web 包内含 `Wiki 提取重点` | 通过 |
| 向导界面 | 混合类型显示「分块设置 + Wiki 设置（提取粒度 / 内容生成要求 0/4000 / 提取重点 / 单次最大页面数）」，截图留档 | 通过 |
| 端到端创建 | 工作台新建「配置验证库」（混合）：分块 3200/200、粒度 详尽、内容要求「使用法务审阅口吻，优先展示责任主体和时间线」、提取重点「重点识别责任主体与资金流向」、最大页面数 24 → 提示「知识库「配置验证库」已创建」 | 通过 |
| WeKnora 侧落地 | 该库 `indexing_strategy` = vector+keyword+wiki+graph；`chunking_config` = `{chunk_size: 3200, chunk_overlap: 200}`；`wiki_config` = `{extraction_granularity: "exhaustive", content_instructions: "使用法务审阅口吻…", extraction_instructions: "重点识别责任主体与资金流向", max_pages_per_ingest: 24, synthesis_model_id: <平台默认>}` | 通过 |
| 清理 | 通过 `DELETE /api/studio/knowledge/bases/config-verify-20260910` 删除（204）；知识库列表恢复为 `hehe` / `weknora-173-verify`（`policy-wiki` 对该账号不可见属既有 ACL 过滤，库本身仍在） | 通过 |

界面操作说明：内置浏览器该 guest 的原生点击/截图不稳定，向导的点击与输入改用页面内 `element.click()` 与原生 value setter + `input` 事件（React 受控组件），并回读输入值与 `aria-pressed`/`0/4000` 计数器确认状态已生效；提交结果与 WeKnora 侧配置为独立核对。

## 6. 未包含与后续

- **合成模型选择**：向导未暴露 `synthesis_model_id` 选择器，仍用平台配置（`HARNESS_WEKNORA_WIKI_SYNTHESIS_MODEL_ID`）。要做需要先有「列出可用模型」的代理端点。
- **创建后修改**：本轮的配置只在创建时下发；WeKnora 支持后续修改（其提示"修改后需重新解析才能影响已有内容"），AXIS 目前没有知识库设置页，因此未接更新路径。
- **知识图谱开关**：混合类型固定开启 `graph_enabled`（沿用既有映射），向导未提供开关。

## 7. 回滚

```bash
cd /data/agent-studio/docker-compose
cp .env.production.bak-kb-config-20260910-132436 .env.production
docker compose -p agent-studio-173 --env-file .env.production \
  -f compose.yaml -f compose.harbor.yaml -f compose.codex-runtime.yaml \
  pull api worker web && docker compose -p agent-studio-173 --env-file .env.production \
  -f compose.yaml -f compose.harbor.yaml -f compose.codex-runtime.yaml \
  up -d --no-build --force-recreate api worker web
```

备份指向 `lead-platform-skills-20260910-r3`（api/worker）与 `citation-doc-20260910-r3`（web）。回滚只影响应用版本：本轮创建的库若已带 `wiki_config`，其配置已写入 WeKnora，回滚不会自动清除。
