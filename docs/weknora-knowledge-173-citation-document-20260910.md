# RAG 引用文档化（引用切片 → 文档抽屉）· 173 部署与验证记录

- 日期：2026-09-10
- 分支：`feature/weknora-knowledge-base`
- 目标环境：`172.20.109.173`（API `:8800`，Web `:3301`）
- 发布 tag：`citation-doc-20260910-r3`（web）；api/worker 保持 `lead-platform-skills-20260910-r3`（我的 tag 下为同一 digest 的重打标签）
- 背景：RAG 模式下的回答引用与 Wiki 模式外观接近（同为下划线文字链接），点开抽屉只显示单个切片；需求是把引用改成"文档形式"——抽屉展示文档全文，并可进一步查看具体切片，同时不影响现有 Wiki 问答。

## 1. 交付内容（仅前端，无后端与数据库改动）

| 位置 | 改动 |
| --- | --- |
| `components/knowledge/citation-document-drawer.tsx(.module.css)`（新增） | 引用以**文档**打开：并行取 `GET /sources/{ref}/documents/{id}`（标题/类型/解析状态/摘要）与 `GET /sources/{ref}/documents/{id}/chunks`（全部切片，按 `seq` 升序）。「全文」视图连续排版，本次引用切片带左侧强调条与「本次引用 · 片段 N」标记，渲染后 `scrollIntoView` 居中；「查看分块」视图按「片段 N」卡片列出并标注本次引用；摘要 3 行折叠；底部显示创建时间、文档 id、引用片段号。左边缘可拖宽（沿用 `citation` 记忆键）。 |
| `components/knowledge/knowledge-citations.tsx` | 抽屉换成新组件，按引用键重建（迟到响应不会覆盖新开的引用）；「检索来源」chip 行保持不变。 |
| `components/knowledge/citation-link.tsx(.module.css)`（新增） | 回答正文里的引用渲染为**文档 chip**（文件图标 + 文档名 + 边框 + 省略号），与 Wiki 实体链接（下划线文字）在视觉上区分。Wiki 链接与 Wiki 抽屉未改。 |
| `lib/studio-client.ts` | 新增 `getKnowledgeDocument`，对应后端已存在的 `GET /v1/studio/knowledge/sources/{reference}/documents/{document_id}`。 |

兼容与边界：

- legacy 快照引用（无 engine 文档）没有文档可开，仍回退到回答携带的摘录；
- 文档重新解析后旧切片 id 失配时，抽屉顶部以虚线框保留回答携带的摘录并注明「当前解析结果中未匹配到该切片」，不让证据静默消失；
- RAG 命中开文档抽屉，Wiki 命中仍开 Wiki 抽屉（概念/实体/摘要页），两条路径互不影响。

## 2. 测试与静态检查

- `tests/knowledge-drawers.spec.tsx` 9 项通过。本轮新增 3 项：文档形式打开（全文含全部切片、「本次引用 · 片段 2」标记、`共 N 个片段`、摘要、全文/查看分块切换）、无 engine 文档时回退摘录、重解析失配时保留摘录。保留原有竞态、单抽屉、Wiki 分支、Portal 焦点循环等用例。
- 前端全量：88 个文件 582 项，579 通过；3 项失败为**改动前既有失败**（已用 `git stash` 在干净工作树复现：`studio-client.spec.ts` 工作区草稿合并、`workbench-layout.spec.ts` 两项源码断言），与本改动无关。
- `npx tsc --noEmit` 通过；Next.js amd64 生产构建通过。

## 3. 真实问答验收（173 工作台，内置浏览器）

用 `kb-citation-verify@0.1.0`（知识库引用验证）新建任务，`@` 选中 `173 验证知识库（混合）`，**关闭 Wiki 开关（RAG 模式）**，提问「以充值油卡为名的非法集资案中，充值金额和返还额度分别是多少？」：

| 验证项 | 结果 |
| --- | --- |
| 检索走 RAG 工具 | 执行记录显示 `mcp__harness-knowledge__query_knowledge_sources`，命中 1 项（未调用 `search_wiki_pages`） |
| 回答引用形态 | 回答正文行内引用渲染为**文档 chip**（文件图标 + 文档名「以充值油卡为名的非法集资」），不再是 Wiki 下划线链接 |
| 点击引用 | 打开抽屉 `引用文档详情`：标题「以充值油卡为名的非法集资」+ 元信息（`173 验证知识库（混合）` / `MANUAL` / `已完成` / `引用片段 100018877`） |
| 全文视图 | `共 1 个片段`，「本次引用 · 片段 100018877」标记 + 案件正文；摘要区显示 500 字文档摘要；底部 `26-09-08 23:43 文档 dffacbf7-… 引用片段 100018877` |
| 查看分块视图 | 切换后按「片段 100018877」卡片展示，卡片带「本次引用」标签，切换态正确 |
| Wiki 回归 | 切到 Wiki 问答的历史任务，点击 `企业工商信息` 实体链接仍打开 `Wiki 页面详情` 抽屉（概念页、分类路径、正文完整） |
| 抽屉清理 | 切换任务后抽屉卸载，无残留 dialog |

页面会话内直连两个接口亦通过（同源真实 Cookie + ACL）：`documents/{id}` 返回 200（title/fileType/parseStatus/500 字摘要）、`documents/{id}/chunks` 返回 200（1 项，字段 `chunkId/content/documentId/seq/sourceReference/tenantId/title`）、`chunks/{chunkId}` 返回 200。

工具限制（记录备查，不影响结论）：内置浏览器该 guest 的原生点击与截图不稳定——Playwright `click()` 在定位/可点性阶段超时、`dom_cua.click()` 返回成功但页面事件探针计数为 0、截图偶发 `browser screenshot activity capture failed for guest`。本轮验收改用页面内 `element.click()`（走真实 React 事件链，探针确认 click 计数 1）触发，并用 DOM 读取 + 截图交叉确认；真实鼠标点击的最终确认由用户在工作台点一下即可。

## 4. 发现的独立问题：合合资料 RAG 检索 0 命中（WeKnora 侧）

排查 RAG 无引用时发现：`合合资料`（WeKnora 库 `4cda2bcd-…`）对 `企业画像 接口`、`启信宝`、`充值油卡 8200` 等查询，`POST /knowledge-bases/{id}/hybrid-search`（`match_count=12`）**全部返回 0 命中**；同批查询在 `weknora-173-verify`（`58d55c21-…`）返回 6 项。该库文档 `副本启信宝API列表-标黄 1.xlsx` 解析完成且切片数 296，但切片不可检索，属数据面的索引/向量构建问题，不在本次前端改动范围。它会导致 RAG 模式下"检索为空、模型给概念化回答"，与本次修复的引用呈现问题是两件事，建议在 WeKnora 侧重查该库的索引状态（或重建知识）。

## 5. 发布步骤（可复现）

```bash
TAG=citation-doc-20260910-r3
# 1) Web 镜像：本机交叉构建 amd64（构建机与 173 都无法访问 docker.io，用运行时复用变体）
docker buildx build --builder agent-deploy-http --platform linux/amd64 --provenance=false \
  --build-arg RUNTIME_BASE=harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:lead-platform-skills-20260910-r3 \
  -f deploy/docker/web-runtime-reuse.Dockerfile \
  -t harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:$TAG --push .
# 2) api 同 tag（重打标签：内容与 r3 运行中的 api 同 digest，不动 r3 的功能改动）
ssh 173 'docker tag .../agent-studio-api:lead-platform-skills-20260910-r3 .../agent-studio-api:'$TAG' && docker push ...'
# 3) 发布（active-run 守卫 + env 备份 + 失败回滚；只重建 web）
ssh 173 'bash /data/agent-studio-builds/'$TAG'/deploy.sh'
```

无数据库迁移，`alembic_version` 保持 0032。

发布过程记录：我的首版 web（`citation-doc-20260910`，11:20）与第二版（`citation-doc-20260910-r2`，11:27）先后被 **11:29:08 的 `lead-platform-skills-20260910-r3` 发布覆盖**（r3 为 api 侧发布，web tag 指回旧 web 镜像），因此运行环境一度回到旧引用抽屉。核对镜像内容确认（旧镜像含 `引用切片详情`、不含 `引用文档详情`）后，按 r3 为基线重出 web 为 `citation-doc-20260910-r3` 并发布，api/worker 未重启。两版之间 `-r2` 增加了"重解析失配保留摘录"，`-r3` 与 `-r2` 前端内容相同。

## 6. 173 实测结论

| 验证项 | 方式 | 结果 |
| --- | --- | --- |
| 服务健康 | `:3301` HTTP 200、`:8800/healthz` 200；web 容器 healthy | 通过 |
| 发布范围 | 仅重建 web（`citation-doc-20260910-r3`）；api 与 3 个 worker 保持 `lead-platform-skills-20260910-r3` 未重启 | 通过 |
| 发布前守卫 | 运行中/排队/待审批任务数 0 | 通过 |
| 镜像内容 | 运行镜像内检出 `引用文档详情`/`未匹配到该切片`/`本次引用`/`查看分块`，**不再含** `引用切片详情`；Wiki 抽屉字符串 `Wiki 页面详情`/`无法确定来源` 仍在 | 通过 |
| 真实问答 + 引用抽屉 | 见 §3（全文、查看分块、Wiki 回归均通过） | 通过 |

## 7. 回滚

```bash
cd /data/agent-studio/docker-compose
cp .env.production.bak-citation-doc-20260910-r3-114447 .env.production
docker compose -p agent-studio-173 --env-file .env.production \
  -f compose.yaml -f compose.harbor.yaml -f compose.codex-runtime.yaml \
  pull web && docker compose -p agent-studio-173 --env-file .env.production \
  -f compose.yaml -f compose.harbor.yaml -f compose.codex-runtime.yaml \
  up -d --no-build --no-deps --force-recreate web
```

备份指向上一版 `lead-platform-skills-20260910-r3`（纯前端改动，回滚不涉及数据）。注意：若之后又有 `lead-platform-skills-*` 之类的整包发布覆盖 web tag，本改动需要按其基线重新出 web 镜像（见 §5 的步骤 1）。
