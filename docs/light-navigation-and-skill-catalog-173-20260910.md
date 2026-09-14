# 浅色任务栏与技能页性能 · 173 环境部署与验证记录

- 日期：2026-09-10
- 分支：`feature/weknora-knowledge-base`
- 目标环境：`172.20.109.173`（API `:8800`，Web `:3301`）
- 发布 tag：`light-nav-skills-20260910`（api 与 web 同 tag）
- 上一版：api `route-guard-20260910-r2`，web `no-upgrade-banner-20260910`

## 1. 交付内容

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M1 | 浅色外观下左侧任务栏的字体与版式与深色外观对齐：行高、内边距、缩进网格、字号字重、状态点位置全部复用深色基线，浅色分支只改配色 | 完成 |
| M2 | 技能目录接口瘦身：`GET /v1/studio/skills/catalog` 不再内联平台包的文件内容 | 完成 |
| M3 | 技能页首屏不再等待全部智能体草稿：平台包先渲染，草稿按并发上限流式并入 | 完成 |
| M4 | 技能列表改为滚动分页（IntersectionObserver + 「加载更多」兜底） | 完成 |

## 2. 浅色任务栏对齐

### 2.1 问题

浅色外观下左侧任务栏与深色外观不是同一套排版：任务标题更小更粗（12.5px/590 对 14px/450）、
行更高（36px 对 30px）、缩进网格不同（项目任务与项目名不对齐）、「新建任务」被渲染成带底色的
42px 药丸、区块标题字号 10.5px（深色 13px）。原因是 `weknora-theme.css` 的浅色皮肤与
`web-codex.css` 的浅色分支各自重新声明了任务栏的尺寸与字重，而这些选择器（`html[data-color-mode="light"] body.codex-theme-v1 …`）
在层叠中高于深色基线规则（`body.codex-theme-v1:has(.console-shell) …`），因此深色基线在浅色下被覆盖。

### 2.2 改法

- `web-codex.css`：删除浅色分支里项目/任务树的几何与字体声明（`.task-list` 内边距、
  `.task-project-head` / `.task-project-heading` 行高与网格、`.task-project-items` 缩进与左边框、
  `.task-list-item` 行高与内边距、`.task-status` 的 `left`、`.task-list-heading` 字重、
  `.task-workbench-name strong` 字重），只保留配色。
- `weknora-theme.css`：删除浅色皮肤里任务栏的密度与字号声明（`.task-sidebar-brand`、
  `.task-sidebar-brand-copy strong`、`.task-sidebar-primary`/`button`、`.task-list-toolbar`、
  `.task-list`、`.task-list-item`、`.task-list-title`、`.account-*` 的几何部分），只保留配色。

深色外观未改动：以上选择器全部带 `html[data-color-mode="light"]` 前缀。

### 2.3 验证

在 173 上对同一会话分别取 `data-color-mode="dark"` 与 `light` 的计算样式，逐项比对
16 个任务栏选择器的字号、字重、行高、内边距、行高、圆角、网格列、`gap` 与包围盒：

```
173 light-vs-dark non-colour diffs: NONE
```

代表值（两色一致）：`.task-list-item` `243x30@10`、`padding: 5px 6px 5px 35px`、
`.task-list-title` `14px/450`、`.task-sidebar-brand` `263x56@0`、
`.task-sidebar-primary > button` `243x38@10`、`.task-status` `6x6@32`。
浅色截图与深色截图逐行对齐，仅配色不同。

## 3. 技能页性能

### 3.1 定位

在 173 上实测 `技能` 页（`/studio/skills`）的资源时序：

| 请求 | 耗时 | 传输量 |
| --- | --- | --- |
| `/api/studio/skills/catalog` | 4615 ms | **10,597,416 B** |
| `/api/studio/drafts`（列表） | 487 ms | 6 KB |
| `/api/studio/drafts/{id}` × 8 | 并行，最长 477 ms | 4–726 KB |

瓶颈不是路由或渲染，而是**平台包目录把每个包的每个文件内容都内联进响应**：22 个包
10.6 MB，其中 `canvas-design`（82 个文件）7.4 MB、`minimax-docx`（73 个文件）1.9 MB。
技能页只用得到文件的**路径**（详情抽屉列出文件名），安装走服务端 `platform_skill_package` 解析内容，
浏览器侧从来不需要文件字节。次要问题是首屏被 `Promise.all(全部草稿)` 阻塞，
且列表一次渲染全部行。

### 3.2 改法

**后端**（`src/harness/studio/models.py`、`platform_skills.py`、`api.py`）：

- 新增列表投影 `PlatformSkillCatalogEntry` / `PlatformSkillListing` / `PlatformSkillListingFile`
  与 `PlatformSkillCatalogListing`，新增 `platform_skill_catalog_listing()`。
- `GET /skills/catalog` 改回该投影：保留全部治理字段（contentHash / license / riskLevel /
  findings / 来源与兼容性 / 评测用例数量）与 Skill 的 `instructions`，文件只保留
  `path` / `binary` / `sizeBytes`。`models.py` 的改动为纯新增，未改动任何持久化模型，
  因此 worker 无需同批滚动（与「catalog 加字段」那次事故不同）。

**前端**（`skills-catalog-page.tsx`、`studio-client.ts`、`skills-catalog-page.module.css`）：

- 平台包与内置 Skill 先渲染（`buildCatalog(packages, [])`），随后按 `DRAFT_FETCH_CONCURRENCY = 4`
  流式读取草稿并增量合并；单个草稿失败只丢它自己，其余目录仍可用，并显示「正在读取智能体技能 n/m」。
- 列表按 `CATALOG_PAGE_SIZE = 20` 分页渲染，底部哨兵进入视口即翻页，同时提供可聚焦的「加载更多」
  按钮作为兜底；搜索或切换作用域时重置页码。
- 平台包的 `files` 类型改为列表投影类型，`fileCount` 取服务端计数并回退到文件数组长度，
  以容忍滚动发布期间的短暂版本错配。

### 3.3 验证

173 实测（真实数据，34 个技能 = 22 个平台包 + 12 个智能体 Skill）：

| 项 | 改前 | 改后 |
| --- | --- | --- |
| `/api/studio/skills/catalog` 传输量 | 10,597,116 B | **208,196 B** |
| 同一请求经 web 代理耗时 | 4,615 ms | **265 ms** |
| 首屏阻塞 | 等待全部 8 个草稿 | 平台包就绪即渲染，草稿流式并入 |
| 列表渲染 | 一次性 34 行 | 首屏 20 行，滚动加载剩余 14 行 |

- 分页：首屏 `已显示 20 / 34` + 「加载更多」；滚动到底后行数升至 34、页脚消失。
- 详情抽屉：`平面视觉设计` 显示包修订/风险/许可证/内容哈希、`Instructions` 11566 字符、
  `附加文件（82）` 并逐条列出 82 个路径，与服务端目录一致。
- 接口自检（镜像内）：`platform_skill_catalog_listing()` 22 个包、199,641 B，
  文件字段集合为 `{path, binary, sizeBytes}`。

## 4. 部署步骤（可复现）

```bash
TAG=light-nav-skills-20260910

# 1) API：应用代码增量镜像（在 173 上构建，只 COPY src/harness，无外部依赖解析）
#    构建上下文 = HEAD 的 src/harness
docker build \
  --build-arg BASE_IMAGE=harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:route-guard-20260910-r2 \
  -f api-code-only.Dockerfile \
  -t harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:$TAG .
docker push .../agent-studio-api:$TAG

# 2) Web：本地 buildx 交叉构建 amd64
#    注意：Docker Hub 不可达，必须显式指定可达镜像源
#    （harbor 的 dependencies-ai/node 只有 arm64 层，用它构建会产出 arm64 运行时而镜像
#     架构标签仍写 amd64 —— 部署到 x86_64 的 173 会 exec format error）
docker buildx build --builder agent-deploy-http --platform linux/amd64 \
  --build-arg NODE_IMAGE=docker.m.daocloud.io/library/node:22-alpine \
  -f deploy/docker/web.Dockerfile \
  -t harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:$TAG --load .
docker push .../agent-studio-web:$TAG

# 3) 发布（active-run 守卫 + env 备份 + 失败回滚，仅重建 api 与 web）
bash /data/agent-studio-builds/$TAG/deploy.sh
```

构建范围：Web 镜像由「HEAD + 本次改动」的独立 worktree 构建，未包含工作区里同分支在途的知识库引用 UI 改动；`c9df6f6` 之后到 HEAD 之间的提交不含 `web/` 变更，因此本次 web 相对线上只多了本记录的改动。

> **勘误（2026-09-14）**：上述收窄构建的判断是错误的，并导致了
> [web 镜像覆盖知识库 Wiki/分块配置表单的事故](incident-173-scoped-web-build-dropped-wiki-config-20260914.md)。
> 本仓库的 web 镜像从**工作区全量**构建，tag 只是标签；因此「HEAD + 本人改动」的镜像会把线上
> 原有的未提交前端工作一并回退掉。后续 web 镜像必须从全量工作区构建。

无数据库迁移（`alembic_version` 保持 0032）：改动只涉及两个只读接口的响应形状与前端。

## 5. 173 实测结论

| 验证项 | 方式 | 结果 |
| --- | --- | --- |
| 服务健康 | api/web healthy，`/healthz` 200 | 通过 |
| 目录瘦身 | `GET /v1/studio/skills/catalog` 返回 22 包 208,196 B，文件字段仅 path/binary/sizeBytes | 通过 |
| 浅色任务栏 | 浅色 vs 深色计算样式 16 项零差异；深浅截图对齐 | 通过 |
| 首屏与分页 | 首屏 20/34 行，滚动加载至 34 行 | 通过 |
| 详情抽屉 | 82 个文件、Instructions、治理字段全部呈现 | 通过 |

## 6. 测试基线

- 后端：`tests/unit/studio/test_platform_skills.py` 7 项通过（新增列表投影契约与文件大小用例）；
  `tests/integration/api/test_agent_studio_api.py` 55 项通过。
- 前端：`tsc --noEmit` 通过；`vitest run` 585 项通过，3 项失败为改动前既有问题
  （`studio-client.spec.ts` 的 `listAccessibleDrafts` 用例、`workbench-layout.spec.ts` 的 2 项
  markdown 预处理用例；已用 stash 复现确认与本次改动无关）。
- `ruff check` 通过。

## 7. 已知边界与后续

- `instructions` 仍随列表返回（22 个包合计约 190 KB，gzip 后约 50 KB）。若后续平台包大幅增多，
  可再拆出 `GET /skills/catalog/{packageId}` 详情接口，让抽屉按需拉取。
- 列表的自动翻页依赖 `IntersectionObserver`；不支持该 API 时退化为「加载更多」按钮。
- 本次只重建 api 与 web，worker 仍运行 `route-guard-20260910-r2`。由于改动未触及持久化模型，
  无需同批滚动；但 `.env.production` 已指向 `light-nav-skills-20260910`，
  该 tag 的 api 镜像已推送，后续任何 `compose up`/`pull` 都不会再遇到缺镜像问题。
