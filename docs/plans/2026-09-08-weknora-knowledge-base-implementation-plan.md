# WeKnora 知识库集成实现计划（功能 1-5）

- 日期：2026-09-08
- 配套设计：[2026-09-08-weknora-knowledge-base-design.md](2026-09-08-weknora-knowledge-base-design.md)
- 分支：`feature/weknora-knowledge-base`
- 变更记录：2026-09-08 决策**首期不对接 IDAAS**（需求 4 拆期：一期按平台用户目录做成员授权，组织树二期另立计划），M4 由 7 人日调整为 5.5 人日，详见设计 §3.4。
- 参照：174 WeKnora `http://172.20.109.174:8180`（页面细节以该实例为准；服务账号在 `.env`，不入库）
- 执行约定：每个任务一个独立 commit；`make verify` + `make web-build` 常绿；涉及 WeKnora 真实调用的测试用录制夹具（respx/vcr），CI 不直连 174。

## M1 网关与 RAG 后台（需求 2）— 约 6 人日

- [ ] T1.1 配置与骨架（0.5d）
  - `src/harness/knowledge/weknora/{__init__,configuration.py,client.py}`；`.env.example` 增加 `HARNESS_KNOWLEDGE_WEKNORA_*`。
  - 验收：`WeknoraClient` 能登录/刷新 token、`list_knowledge_bases` 解析成功（对照 174 实测响应夹具 `tests/fixtures/weknora/*.json`）。
- [ ] T1.2 端口与映射（1d）
  - `knowledge/ports.py` 增加 `KnowledgeEnginePort`；`weknora/gateway.py` 实现建库/删库/上传/列表/进度/重解析/切片/检索；`mapping.py` 状态映射（`parse_status` → `KnowledgeSyncStatus`）。
  - 验收：单测覆盖映射表与错误分支（404/401/超时）。
- [ ] T1.3 组合根接线（0.5d）
  - `composition.py` 按 settings 装配网关；`KnowledgeService` 按 `engine` 分派 `search`（weknora → `POST /knowledge-bases/:id/hybrid-search`）。
  - 验收：fake 组合根单测通过；Postgres 组合根冒烟。
- [ ] T1.4 状态跟踪镜像（1.5d）
  - sync worker 轮询 weknora 文档状态与 `stages/spans`，写 `KnowledgeSyncRun`；`GET /v1/studio/knowledge/syncs` 返回阶段明细。
  - 验收：上传→轮询→完成状态机单测；失败重试与取消路径。
- [ ] T1.5 切片代理（1d）
  - `GET /v1/studio/knowledge/sources/{ref}/chunks`、`GET .../chunks/{chunk_id}`（ACL 复查后代理 `GET /chunks/:knowledge_id`、`/chunks/by-id/:id`）。
  - 验收：无权限 403；切片分页；与 174 实测响应结构一致。
- [ ] T1.6 文档写入代理（1.5d）
  - `POST /sources`（engine=weknora）→ `knowledge/file|url|manual` multipart 转发；删除/重解析代理；输入产物（InputArtifact）落地后上传。
  - 验收：端到端对 174 上传一份 markdown，状态变为 completed，切片可见（人工冒烟 + 录制夹具）。

## M2 目录与卡片（需求 1 + 需求 2 UI）— 约 4 人日

- [ ] T2.1 kb_type 模型与迁移（1d）
  - `KnowledgeBase` 增 `kb_type/engine/engine_ref/capabilities` 投影；迁移 `00xx_knowledge_kb_type.py`；`storage/models.py` 同步列。
  - 验收：alembic upgrade/downgrade；旧数据默认 `engine=legacy, kb_type=rag`。
- [ ] T2.2 bases API 扩展（0.5d）
  - 列表/详情/创建/更新支持新字段；创建 weknora 库先调网关再落目录（失败回滚）。
- [ ] T2.3 卡片页重构（1.5d）
  - `web/harness-console/src/app/studio/knowledge/page.tsx`：页签过滤（全部/我创建的/收藏/本空间）+ 卡片（类型徽标/能力图标/文档数/同步状态点/创建者）。
  - 参照：WeKnora `/platform/knowledge-bases` 卡片分组布局（截图存 `docs/manual-assets/2026-09-08/`）。
  - 验收：`make web-test` 组件测试；三类徽标渲染正确。
- [ ] T2.4 新建向导与 KB 详情骨架（1d）
  - 新建向导（类型三选一 → 命名 → 分块/抽取配置）；KB 详情页路由 `studio/knowledge/[ref]`，三页签骨架（文档/Wiki/图谱，M3 前 Wiki/图谱占位）。
  - 文档页签：列表 + 状态过滤（全部状态/来源/类型）+ 上传按钮 + 文档抽屉（基本信息/摘要/全文/分块切换）。

## M3 Wiki 与图谱（需求 3）— 约 6 人日

- [ ] T3.1 wiki 代理端点（1d）
  - `/v1/studio/knowledge/bases/{ref}/wiki/{pages,index,folders,graph,stats,search}` 只读代理 + ACL。
- [ ] T3.2 Wiki 页签（2d）
  - 左栏分类目录树（`category_path`）+ 搜索；右栏索引页（摘要/实体/概念分节）；`[[wikilink]]` 渲染站内跳转；页面详情抽屉（Markdown、aliases、回链文档）。
  - 参照：WeKnora KB 详情 Wiki 视图。
- [ ] T3.3 图谱页签（2d）
  - 引入 `@antv/g6` v5；力导向布局；按 `page_type` 着色（摘要蓝/实体绿/概念橙）；点击节点开页面抽屉；全库概览/适应屏幕控件；节点>500 分页。
  - 参照：WeKnora KB 详情图谱视图（图例与控件布局）。
  - 验收：`web-build` bundle 预算检查（G6 ≤ 200KB gzip）。
- [ ] T3.4 hybrid 聚合检索（1d）
  - `search(mode=hybrid)`：并行 hybrid-search + wiki/search，加权融合（0.6/0.4 可配），命中标记 `chunk|wiki_page`；单测覆盖融合与去重。

## M4 权限与问答引用（需求 4、5；首期不含 IDAAS）— 约 5.5 人日

- [ ] T4.1 kb_members 模型与迁移（1d）
  - `kb_members(kb_reference, subject_type user|org_unit, subject_id, org_path, role viewer|editor, ...)`；`org_unit/org_path` 为二期 IDAAS 预留，一期只写 `user`；effective 权限解析器一期实现"直授覆盖继承"骨架（继承分支空实现）。
- [ ] T4.2 平台用户目录（0.5d）
  - `IdentityDirectoryPort` 一期实现 = AXIS `users` 表（`resolve_users`、`search_users`）；不引入 `HARNESS_IDAAS_*` 配置。
- [ ] T4.3 成员 API 与执行收口（1.5d）
  - `GET/POST/PUT/DELETE /v1/studio/knowledge/bases/{ref}/members`（批量：`user_ids[]|emails[]` + role，无效条目忽略并返回清单）；`KnowledgeService` 全部读写路径统一走 effective 成员校验。
  - 验收：无 viewer 隐藏目录；viewer 访问写接口 403；重复授权幂等。
- [ ] T4.4 成员管理 UI（1d）
  - KB 设置新增"成员管理"：用户搜索多选 + 粘贴邮箱批量添加（选查看/编辑角色）；成员表（角色下拉、移除）。页签左侧预留组织树位置给二期。
- [ ] T4.5 问答引用链路（1d）
  - `knowledge/runtime.py` `query_knowledge_sources` 返回结构化引用（citation_index/chunk_id/knowledge_id/document_title/content/score）；`agui/activity.py` 事件附带 `citations`；线程历史缓存兼容回放。
- [ ] T4.6 引用前端（0.5d）
  - `citation-chip.tsx`：`[n]` 徽标渲染；点击开切片抽屉（文档标题/KB/切片全文/在知识库中打开）；打开前权限探测（403 隐藏内容）。

## 验收联调（0.5 里程碑，2 人日）

- 对 174 实例端到端脚本：建 rag / wiki / hybrid 三库 → 上传文档 → 状态跟踪至 completed → 查看切片 → wiki 索引与图谱渲染 → 配置成员（批量直授 + 角色变更校验）→ 会话提问验证引用切片查看。
- 回归：legacy 引擎知识库（file/web 连接器、团队空间共享）不受影响；`make verify`、`make e2e`、`make web-test`、`make web-build` 全绿。

## 排期汇总

| 里程碑 | 人日 | 累计 |
| --- | --- | --- |
| M1 网关与 RAG 后台 | 6 | 6 |
| M2 目录与卡片 | 4 | 10 |
| M3 Wiki 与图谱 | 6 | 16 |
| M4 权限与问答引用（不含 IDAAS） | 5.5 | 21.5 |
| 验收联调 | 2 | 23.5 |

并行建议：T4.1/T4.2（权限模型与用户目录）可与 M2/M3 并行；前端 G6 选型 PoC（T3.3 前置）可在 M1 期间先行验证。

## 二期预留（IDAAS 接入，另立计划）

一期交付后按需启动，模型与 API 契约不变：IDAAS HTTP 适配器实现 `IdentityDirectoryPort` 的 `get_org_tree()`/`list_users_under(path, recursive)`（配置 `HARNESS_IDAAS_BASE_URL/TOKEN`）；成员管理页左侧组织树（懒加载/搜索、按组织子树批量授权）；`org_unit` 继承授权生效（填充 effective 权限解析器继承分支）；`HARNESS_IDAAS_*` 配置与目录同步任务。粗估 3-4 人日（依赖 IDAAS 接口规格）。
