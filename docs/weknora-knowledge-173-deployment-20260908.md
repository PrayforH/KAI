# AXIS WeKnora 知识库集成 · 173 环境部署与验证记录

- 日期：2026-09-08
- 分支：`feature/weknora-knowledge-base`
- 目标环境：`172.20.109.173`（KAI WORKBENCH 黑色主题，Web `:3301`，API `:8800`）
- 知识数据面：`172.20.109.174:8180` WeKnora（服务账号，终端用户不直连）
- 发布 tag：`knowledge-graph-3d`（web；api 仍为 `weknora-kb-20260910-cardmenu`）
- 配套设计：[2026-09-08-weknora-knowledge-base-design.md](2026-09-08-weknora-knowledge-base-design.md)、[实现计划](2026-09-08-weknora-knowledge-base-implementation-plan.md)

## 1. 交付内容

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M1 | WeKnora 网关（client/gateway/port）、文档与切片代理、状态镜像、检索分派 | 完成 |
| M2 | `kb_type`/`engine` 模型、卡片式知识库控制台、新建向导、KB 详情（WeKnora 式文档工作区） | 完成 |
| M3 | Wiki 页面/索引/统计/图谱代理、Wiki 页签、AntV G6 图谱页签 | 完成 |
| M4 | `kb_members` 成员权限（查看/编辑）、平台用户目录、引用切片链路 | 完成 |

## 2. 部署步骤（可复现）

### 2.1 构建与推送镜像

API 采用"基于已验证运行镜像的增量镜像"，只替换应用代码与迁移，避免重新拉取基础镜像：

```bash
# 1) 在 173 上构建 API 增量镜像（原生 amd64）
scp src 与 migrations 打包到 173:/data/agent-studio-builds/<tag>/
docker build \
  --build-arg BASE_IMAGE=harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:ui-polish-20260831-ce72cc6 \
  -t harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:<tag> .
```

增量镜像额外安装基础镜像缺少的两个依赖：`pgvector`（记忆库向量列）与 `libarchive-c`（沙箱归档工具）。

```bash
# 2) 本地交叉构建 Web（builder 走原生架构，仅运行层为 amd64）
docker buildx build --platform linux/amd64 -f deploy/docker/web.Dockerfile \
  -t harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:<tag> --load .
docker push harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:<tag>
```

### 2.2 PostgreSQL pgvector（一次性）

173 的 PostgreSQL 18.1 数据卷早于 pgvector。迁移 0031 需要 `CREATE EXTENSION vector`，否则 `alembic upgrade` 失败。

```bash
# 构建 pgvector 镜像并装入运行中的容器（不重建数据库、不动数据卷）
docker build -f deploy/memory-v2/postgres.Dockerfile \
  -t harbor.shdata.com:5000/agent-studio/amd64/axis-postgres:18.1-vector0.8.6 .
docker cp <extract>:/usr/lib/postgresql/18/lib/vector.so <postgres>:/usr/lib/postgresql/18/lib/
docker cp <extract>:/usr/share/postgresql/18/extension/vector* <postgres>:/usr/share/postgresql/18/extension/
docker exec <postgres> psql -U harness -d harness -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

`docker-compose/compose.postgres-pgvector.yaml` 已记录该镜像；**postgres 容器未来重建时必须把该覆盖文件加入 compose 链**，否则扩展会随容器丢失。

### 2.3 配置与发布

```bash
cd /data/agent-studio/docker-compose
cp .env.production .env.production.bak-<timestamp>
# 新增（密码走 .env.production，不写入仓库/脚本）
HARNESS_WEKNORA_BASE_URL=http://172.20.109.174:8180
HARNESS_WEKNORA_EMAIL=<service account>
HARNESS_WEKNORA_PASSWORD=<from secure channel>
HARNESS_WEKNORA_EMBEDDING_MODEL=builtin-bge-m3-v2
HARNESS_WEKNORA_SUMMARY_MODEL_ID=<summary model id>
HARNESS_WEKNORA_WIKI_SYNTHESIS_MODEL_ID=<wiki synthesis model id>
sed -i 's|^HARNESS_HARBOR_IMAGE_TAG=.*|HARNESS_HARBOR_IMAGE_TAG=<tag>|' .env.production

COMPOSE="docker compose -p agent-studio-173 --env-file .env.production \
  -f compose.yaml -f compose.harbor.yaml -f compose.codex-runtime.yaml"
$COMPOSE run --rm migrate          # 0030 -> 0031 -> 0032
$COMPOSE pull api worker web
$COMPOSE up -d --no-build --force-recreate api worker web
```

## 3. 173 实测结论

| 功能 | 验证方式 | 结果 |
| --- | --- | --- |
| 迁移 | `alembic_version` = 0032 | 通过 |
| 服务健康 | api `:8800/healthz` 200、web `:3301` 200、worker×3 healthy | 通过 |
| WeKnora 连通 | API 容器读取 `HARNESS_WEKNORA_*` 并列出远端知识库 | 通过 |
| 知识库卡片控制台 | 黑色主题下渲染、类型徽标（混合）、WeKnora 引擎标记 | 通过 |
| 新建向导 | 选「混合」→ 创建 → 绿色成功提示 → 卡片出现 | 通过 |
| 文档工作区 | WeKnora 式：上传提示 + 搜索/状态筛选 + 卡片网格（复选框/状态/类型/时间）+ 批量栏（重建知识/批量删除） | 通过 |
| 文档解析 | 手动建文档 → `processing` → `completed`（含摘要） | 通过 |
| Wiki 页签 | 分类树（索引/摘要/实体/概念）+ 索引页 + 统计「共 6 页 · 引用 17 条」 | 通过 |
| 图谱页签 | G6 力导向图，图例（摘要蓝/概念橙/实体绿/索引灰），6/6 节点 · 17 引用 | 通过 |
| 成员管理 | 目录搜索命中真实用户 → 添加为查看者 → 成员（1）列表 + 角色下拉 + 移除 | 通过 |
| 问答引用切片 | 发布测试智能体 `kb-citation-verify@0.1.0`（绑定本知识库）→ 提问 → 工具命中 1 项 → 回答带 [1][2] → 回复下方引用徽标 ①1.00/②0.99 → 点击打开切片抽屉（含全文） | 通过 |
| 侧栏搜索位置 | 搜索入口移到侧栏头部，位于收起按钮左侧（紧凑图标按钮） | 通过 |
| 侧栏导航顺序 | 「知识库」位于「新建任务」下方第二项，其后为智能体、技能 / MCP | 通过 |
| 模型选择器对齐 | 模型名与下拉箭头紧贴、整体靠右对齐（按选中项自适应宽度，不再是固定最宽选项宽度） | 通过 |
| 版本选择器图标 | 顶部版本选择器增加 git 分支图标（对齐 Codex 分支控件样式） | 通过 |
| 侧栏搜索贴近收起 | 搜索与收起按钮边缘间距 0，图标视觉相邻 | 通过 |
| `@` 指定知识库（多选） | 输入 `@` 列出知识库，回车切换选中并保持列表打开；底栏知识库控件显示已选，选择随 `forwardedProps.knowledgeReferences` 生效 | 通过 |
| 详情页头部扁平化 | 标题/元信息/页签压缩为紧凑一行，文档工作区上移 | 通过 |
| 知识库卡片改版 | 图标 + 标题 + 描述 + 文档数徽章（绿色），保持黑色配色；文档数取最近一次同步 | 通过 |
| 卡片去下划线 | 标题/描述/标识不再出现链接下划线 | 通过 |
| 成员管理外移 | 成员管理从详情页页签移到知识库卡片，点击在弹窗中管理（已添加成员可见） | 通过 |
| 详情页精简 | 去掉 `reference · engine · kbType` 与描述行，上传提示上移到知识库名下方；页签仅保留 文档/Wiki/图谱 | 通过 |
| 文档卡片交互 | 点击卡片即查看；`⋯` 菜单含 查看 / 重新解析 / 删除 | 通过 |
| 文档查看器改版 | 分区块（基本信息 / 摘要 / 文档内容）：绿色竖线标题、类型与状态徽章、摘要框、全文 / 查看分块切换 | 通过 |
| Wiki 页签对齐 | 左栏 索引/目录 入口 + 知识/摘要 页签（绿色选中线）+ 分类计数徽章；页面链接绿色带下划线 | 通过 |
| 图谱页签对齐 | 右侧图例面板（摘要/概念/实体/索引）+ 适应屏幕 / 隐藏箭头 + 全库概览节点数；左上 Wiki 搜索框 | 通过 |
| 图谱无边框放大 | 画布去掉边框、按面板高度铺满 | 通过 |
| 文档摘要展示 | 抽屉「摘要」区块展示 WeKnora 文档摘要（实测手动文档有摘要） | 通过 |
| 表格文档渲染 | 抽屉内容按 Markdown 渲染，表格文档显示为真实表格（实测 3 列分类表） | 通过 |
| 分页修复 | Wiki 页面与文档列表按页拉取：合合资料 66 页（知识 64 + 摘要 1 + 索引 1）、248 件库 248 文档，不再截断到 20 | 通过 |
| 图谱节点抽屉 | 点击节点从右侧展开全高抽屉，展示页面摘要、分类路径与可点击链接 | 通过 |
| Wiki 分类树 | 左栏按 WeKnora `category_path` 一级分类分组（如 企业画像/企业风险）并显示计数，不再只有实体/概念 | 通过 |
| Excel 表格视图 | WeKnora 切片把表格压成 `A: 值,B: 值` 文本，无法渲染表格；改为下载原文件解析（xlsx→openpyxl、xls→xlrd、csv），抽屉新增「表格」视图 | 通过 |
| 抽屉知识互链 | 抽屉正文渲染加粗、行内代码与全部 `[[wikilink]]`，点击在抽屉内跳转到目标页面（实测 养老服务 → 预付费返利） | 通过 |
| 摘要折叠 | 抽屉摘要默认 3 行，右下角箭头展开/收起 | 通过 |
| 表格单元格省略 | 单元格单行显示，超出宽度以省略号截断（nowrap + ellipsis + max-width 260px） | 通过 |
| 分类树缩进 | 分类下的页面条目缩进一级显示 | 通过 |
| 拖拽上传 | 文档页签支持把文件拖入区域上传（支持多文件） | 通过 |
| 关键词内联 | Wiki 正文里的知识关键词不再独占一行，随句子内联展示并可点击 | 通过 |
| 抽屉类型标注 | 图谱抽屉标题下方以彩色徽章标注页面类型（摘要/实体/概念/索引） | 通过 |
| Excel 默认表格 | 表格类文档打开抽屉即进入「表格」视图；摘要默认收起 3 行、箭头展开 | 通过 |
| 文档数量实时 | 列表接口并发读取 WeKnora 真实文档数并回写缓存（实测 合合资料 1、173 库 3，与 WeKnora 一致） | 通过 |
| 批量管理模式 | 「⋯」菜单新增「批量管理」，进入后勾选框才出现，悬浮栏保持旧顺序（重建知识 / 批量删除 靠右） | 通过 |
| Wiki 加粗渲染 | 正文 `**加粗**` 与行内代码正确渲染，不再显示字面星号 | 通过 |
| RAG/Wiki 问答模式 | 编辑器新增「RAG 问答 / Wiki 问答」切换，Wiki 模式走 `search_wiki_pages`（进程内 SDK MCP 与远端 HTTP MCP 均已提供） | 通过 |
| Wiki 答案可点击 | 回答中的 `[[slug|title]]` 渲染为可点击链接（react-markdown 默认清洗会清空 `wiki:` 协议，已用 urlTransform 放行）；点击打开页面抽屉，抽屉内可继续跳转 | 通过 |
| 链接配色统一 | Wiki 链接改为主题白色 + 淡下划线，去掉绿色/橙色，与黑色主题一致 | 通过 |
| Wiki 回答更丰富 | 工具先返回知识库索引页作为全局地图，默认取 12 页/每页 8k 字符；契约要求 2-4 次定向检索 + 结构化作答（实测单次回答约 4.5k 字符、分节呈现） | 通过 |
| 引用可打开 | 抽屉自行解析知识库（不依赖编辑器当前选择）；会话的知识库选择与问答模式按 thread 持久化，刷新不丢 | 通过 |
| `@` 选择知识库 | 编辑器知识库控件改用 `@` 图标，与提示「/ 命令 · @ 知识库 · $ 技能」一致 | 通过 |
| 模式切换精简 | 问答模式只显示 RAG / Wiki 短标签（宽度约 83px） | 通过 |
| 抽屉可调宽 | Wiki 抽屉 / 引用抽屉 / 文档抽屉左边缘可拖拽调宽，Wiki 抽屉实测 360–1000px 夹紧，宽度按抽屉记忆 | 通过 |
| 工具栏顺序 | 编辑器底栏顺序为 `+ 附件 · @ 知识库 · 智能体 · Wiki 开关`，问答模式改为紧凑开关（非下拉） | 通过 |
| 控件紧凑化 | 知识库控件只显示放大的 `@` + 已选数量（宽度约 51px）；知识库下拉列表收窄；工具栏按钮间距收紧；侧栏搜索与收起按钮间距 8px | 通过 |
| 登录页 logo | 中间卡片左上角由字母占位改为真实产品标识 `/brand/kai-mark-v2.png` | 通过 |
| 删除文档 | 点击删除后卡片立即消失、计数递减并提示「文档已删除」；WeKnora 删除是异步任务（返回 `task_id`，列表短时间内仍含该行），重复删除同一文档不再报 404（引擎侧把 404 视为已删除） | 通过 |
| 删除空响应 | 删除接口返回 `204 No Content`，前端此前对空响应仍调用 `response.json()` 会抛 `Unexpected end of JSON input`，导致删除成功却显示报错；客户端统一改为容忍 204/空体 | 通过 |
| 拖拽上传默认行为 | 文件拖到页面任意位置不再被浏览器打开/下载（窗口级 `dragover`/`drop` 阻止默认行为），拖入文档区正常上传（实测 `拖拽验证-20260909.txt` 上传并进入解析） | 通过 |
| 图谱动效升级 | 基于 G6 v5 原生能力重构：实例挂载一次、数据经 `setData` 增量更新；力导向逐帧动画入场；悬停经 `hover-activate`（degree 1）高亮邻居并压暗无关节点/边；节点按连接度分级尺寸、标签带深色底板、active/selected 态带光晕；`fitView`/`focusElement` 带缓动动画；箭头切换经 `updateEdgeData` 原地改样式不重排；已见节点保留画布位置，展开新邻居不闪动 | 通过 |
| 图谱展开邻居 | 双击节点进入探索模式：揭示该节点及其直接邻居，继续双击已见节点逐跳外扩；侧栏出现「显示全部」重置与「双击节点继续展开邻居」提示；单击（240ms 消歧）仍打开页面抽屉。实测 16→10→重置 16 | 通过 |
| 知识库卡片更多菜单 | 「成员管理」「删除」收进卡片右上角 ⋯ 菜单；点击卡片其他区域或按 Esc 收起（实测 外点 0 项→重开 2 项→Esc 0 项）；删除仅管理角色可见 | 通过 |
| 删除知识库 | 新增 `DELETE /v1/studio/knowledge/bases/{reference}`（204）：先删 WeKnora 远端库（404 幂等）再清本地 base/source/成员/遗留切片，全程审计；确认弹窗明示不可恢复。实测建「删除探针-临时」→菜单删除→卡片消失、计数 3→2、WeKnora 侧无残留 | 通过 |

| 3D 可行性分析 | 调研开源方案：G6 v5 仅覆盖 2D；选型 `3d-force-graph` 1.80（Three.js + d3-force-3d，vasturiano 出品，业界知识图谱 3D 事实标准，three-forcegraph/three-render-objects/three-spritetext 配套完整）。数据结构与现有 Wiki 图谱同构（nodes/links），无需后端改动；three ~150KB gzip 走动态 import 仅在 3D 模式加载，2D 模式零影响。结论：可行，按 2D/3D 双引擎实现 | 通过 |
| 3D 图谱视图 | 侧栏「切换 3D/2D 视图」双向切换并按 localStorage 记忆；3D 力导向（orbit 相机、拖拽旋转、滚轮缩放），复用 2D 的过滤/搜索/展开邻居/页面抽屉与全库概览统计 | 通过 |
| 3D 复杂特效 | UnrealBloomPass 辉光（彩色发光节点）、连线粒子流（可开关，悬停邻居加速增亮）、星尘背景 320 点、SpriteText 深色底板标签、节点尺寸按连接度分级、摘要节点加大 | 通过 |
| 3D 悬停高亮 | `onNodeHover` 邻居高亮/其余压暗（节点透明度 + 标签透明度 + 连线增亮 + 粒子增强），实测 CUA 悬停生效 | 通过 |
| 3D 点击/双击 | 单击打开页面抽屉、双击展开邻居（320ms 消歧），采用原生 click 事件 + `graph2ScreenCoords` 屏幕空间命中（26px 容差），规避库内 press/release 管线的不确定性；展开后相机飞行至锚点节点。实测 16→10→重置 16、抽屉正确打开且双击无误开 | 通过 |
| 3D 缩放保持 | 修复手动放大后被自动拉回：`onEngineStop` 与兜底定时器的 zoomToFit 在用户相机操作后全部停用（wheel + OrbitControls `start` 置位 userNavigated，「适应屏幕」按钮不受限）。实测滚轮放大后等待 10s 视图保持不变 | 通过 |
| 3D 节点造型 | 节点由球体升级为按类型区分的多面体模型：摘要=二十面体、实体=八面体、概念=十二面体、索引=立方体、页面=球体；flat shading + 自发光（emissive 0.45）+ 1.45x 线框外壳，辉光下剪影清晰且类型可辨识；悬停压暗覆盖外壳材质 | 通过 |
| 3D 视口修复 | three-render-objects 宽高默认取 window 尺寸，导致画布 1280×720 溢出、构图偏移；显式 `width/height` 绑定容器并监听 resize。另补充 1.4s/3.6s 兜底 zoomToFit 与斥力收敛（-220），孤立索引节点不再漂移撑破包围盒 | 通过 |

## 4. 待验证项与后续动作

1. **测试智能体**：`kb-citation-verify@0.1.0` 是本次验证产物，绑定 `weknora-173-verify` 知识库；如需清理，删除该草稿/版本即可（不影响其他智能体）。
2. **质量同步容器**：`agent-studio-173-quality-sync-1` 仍为旧镜像，本轮未纳入发布（与知识库无关）。
3. **IDaaS 组织树**：二期，`kb_members` 已预留 `org_unit`/`org_path`。
4. **Codex 运行时**：本轮按需求不处理。已知限制——Codex 运行时未接入知识 MCP，其上的智能体选择 RAG/Wiki 问答会静默无效（工具不存在）；编译器也会拒绝带 manifest 级 `knowledge_references` 的 Codex 智能体。

## 5. 回滚

```bash
cd /data/agent-studio/docker-compose
cp .env.production.bak-<timestamp> .env.production   # 恢复上一个 tag
$COMPOSE pull api worker web && $COMPOSE up -d --no-build --force-recreate api worker web
```

数据库迁移 0032 只新增 `knowledge_base_members` 表，不修改既有表；回滚应用版本不影响该表。
