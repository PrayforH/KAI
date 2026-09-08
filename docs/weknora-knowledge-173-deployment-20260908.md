# AXIS WeKnora 知识库集成 · 173 环境部署与验证记录

- 日期：2026-09-08
- 分支：`feature/weknora-knowledge-base`
- 目标环境：`172.20.109.173`（KAI WORKBENCH 黑色主题，Web `:3301`，API `:8800`）
- 知识数据面：`172.20.109.174:8180` WeKnora（服务账号，终端用户不直连）
- 发布 tag：`weknora-kb-20260908-bbd40e6`（api 与 web 同 tag）
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
| 侧栏搜索位置 | 搜索入口移到侧栏头部，位于收起按钮左侧（紧凑图标按钮）；原位置改为「知识库」导航项 | 通过 |
| 登录页 logo | 中间卡片左上角由字母占位改为真实产品标识 `/brand/kai-mark-v2.png` | 通过 |

## 4. 待验证项与后续动作

1. **测试智能体**：`kb-citation-verify@0.1.0` 是本次验证产物，绑定 `weknora-173-verify` 知识库；如需清理，删除该草稿/版本即可（不影响其他智能体）。
2. **质量同步容器**：`agent-studio-173-quality-sync-1` 仍为旧镜像，本轮未纳入发布（与知识库无关）。
3. **IDaaS 组织树**：二期，`kb_members` 已预留 `org_unit`/`org_path`。

## 5. 回滚

```bash
cd /data/agent-studio/docker-compose
cp .env.production.bak-<timestamp> .env.production   # 恢复上一个 tag
$COMPOSE pull api worker web && $COMPOSE up -d --no-build --force-recreate api worker web
```

数据库迁移 0032 只新增 `knowledge_base_members` 表，不修改既有表；回滚应用版本不影响该表。
