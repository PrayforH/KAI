# 平台 Skill 目录化（方案 A）· 173 环境部署与验证记录

- 日期：2026-09-10
- 分支：`feature/weknora-knowledge-base`（与 WeKnora 在途改动同树构建）
- 目标环境：`172.20.109.173`（API `:8800`，Web `:3301`）
- 发布 tag：`skill-catalog-20260910`（api 与 web 同 tag；上一版 `weknora-kb-20260910-cardmenu`）

## 1. 交付内容

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M1 | Skill 提升为能力目录一等资源：`SkillCapability` 进入 `CapabilityCatalog.skills`，`GET /v1/studio/catalog` 直接可见，merge-on-read 自动把新增平台包播种进系统与租户管理目录 | 完成 |
| M2 | 草稿引用式挂载：`AgentDraftSpec.skillReferences`（按 packageId 引用），编译期 `AgentDraftCompiler.resolve_skills` 从平台包目录解析内容并以 `DraftSkillSource`（packageId@revision + contentHash）钉入产物，保持已发布版本可复现 | 完成 |
| M3 | 校验闭环：未知/已禁用/平台包缺失/运行时不兼容/与快照同名冲突的引用在 `validate` 产生 ERROR；`catalog impact` 支持 `skill` 资源类型（受影响草稿分析）；`disable` 支持租户级停用 | 完成 |
| M4 | 权限收紧：`POST /drafts/{id}/skills/catalog/{package}/install`（快照导入）与新增 `PUT /drafts/{id}/skills/references`（引用绑定）均要求 `studio:catalog:write`（owner/admin）；普通 member 只能上传自己的 Skill zip；前端 `canManageCatalog` 对齐，member 看到提示而非 403 | 完成 |
| M5 | 办公 Skill 入库（4 个，全部为平台原创中文文本、Apache-2.0）：`office-docx` / `office-xlsx` / `office-pptx` / `office-pdf`，方法论参考 anthropics/skills（175k stars）document skills，上游以 pinned commit `41bbe19d` 声明；未复制其受自定义许可的文本/脚本；不含模型专有依赖，GLM/DeepSeek 均可用（沙箱内 python-docx / openpyxl / python-pptx / pypdf，先探测后降级） | 完成 |

## 2. 设计要点

- **目录只存元数据**：`SkillCapability` 携带 packageId/revision/contentHash/compatibleRuntimes/enabled 等；内容始终由 `default_platform_skill_catalog()` 按 packageId 解析。目录升级随 merge-on-read 自动传播，草稿只声明引用。
- **编译期钉版**：解析出的 Skill 以快照写进 bundle（`skills/<name>/SKILL.md` + metadata.harness.source），运行时与既有格式完全兼容；平台目录后续升级不影响已发布版本。
- **upsert 对 skill 关闭**：平台 Skill 身份由平台包目录治理，租户只能 `disable/enable`（走 `/catalog/{type}/{id}` 禁用端点），请求模型层直接拒绝 `SkillCapability` 进 upsert。

## 3. 部署步骤（可复现）

```bash
TAG=skill-catalog-20260910
# 1) API 增量镜像（173 上构建，SDK 保持基座的 0.2.128 不变）
scp src/harness 打包 + deploy/docker/api-update.Dockerfile → 173:/data/agent-studio-builds/$TAG/
docker build \
  --build-arg BASE_IMAGE=harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:weknora-kb-20260910-cardmenu \
  --build-arg SDK_VERSION=0.2.128 \
  -f api-update.Dockerfile \
  -t harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:$TAG .
docker push .../agent-studio-api:$TAG

# 2) Web 镜像（本地 buildx 交叉构建 amd64）
docker buildx build --platform linux/amd64 -f deploy/docker/web.Dockerfile \
  -t harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:$TAG --load .
docker push .../agent-studio-web:$TAG

# 3) 发布（active-run 守卫 + env 备份 + 失败回滚，仅重建 api web；worker 保持 v5 基线）
/data/agent-studio-builds/$TAG/deploy.sh
```

无数据库迁移：草稿 spec 为 JSON payload，`skillReferences` 自动随模型序列化；目录 skill 播种走 merge-on-read，`alembic_version` 保持 0032。

## 4. 173 实测结论

| 验证项 | 方式 | 结果 |
| --- | --- | --- |
| 服务健康 | `:8800/healthz` 200、web `:3301` 200、api/web 容器 healthy | 通过 |
| 平台包目录 | `GET /v1/studio/skills/catalog` 返回 9 个包（含 4 个办公包） | 通过 |
| 能力目录播种 | `GET /v1/studio/catalog` 的 `skills` 数组自动含全部 9 项且 enabled | 通过 |
| 引用挂载端到端 | 新建草稿 → `PUT /drafts/{id}/skills/references`（4 个办公包）→ 拉取 bundle：`skills/office-{docx,xlsx,pptx,pdf}/SKILL.md` 全部物化，frontmatter 含 name/description/来源 metadata，`agent.yaml` skills 列表 4 项 | 通过 |
| 草稿规格 | 草稿 `skillReferences` 保留声明式引用，`skills` 快照数组为空 | 通过 |
| 测试残留 | 验证草稿已 `DELETE`（204），环境无残留 | 通过 |

## 5. 测试基线

- `tests/unit/studio` + `tests/integration/api/test_agent_studio_api.py`：228 通过（含新增用例：目录播种合并、编译解析/禁用/未知引用、references 端点端到端、权限矩阵）。
- `tests/unit` 全量：1062 通过；3 个失败（`test_lifecycle`、`test_final_readiness`、`test_migration_0028`）在改动前的 HEAD 上同样失败，与本次无关。
- 前端 `tsc --noEmit` 通过。

## 6. 已知边界与后续

- `GET /skills/catalog`（平台包）仍为公开只读；member 上传自己的 zip 快照路径不变。
- 发布评测用例目前只有正向触发用例；负向（不应触发）用例待 EvalExpectation 支持「输出不含」类断言后补充。
- `skillCount` 卡片统计只计快照 skill，不含目录引用（引用在编译期物化，草稿层面保持声明式）。

## 7. 事故记录：worker 版本偏斜导致全量运行秒败（已修复）

- 现象：发布后约 9 小时（09:50–09:52），用户运行全部在数百毫秒内失败，`error_code=runtime_error`。
- 根因：worker 停留在旧镜像（旧 `CapabilityCatalog` 模型没有 `skills` 字段），而新 API 已把含 `skills` 的目录记录写入数据库；worker 反序列化时 pydantic `extra_forbidden` 直接崩溃。**经验：catalog 模型加字段属于读写双向变更，api 与 worker 必须同批滚动**，"只重建 api/web 最小影响面"的判断不成立。
- 复杂因素：另一条工作线（知识图谱 3D）于 04:54 将 `.env.production` 的 `HARNESS_HARBOR_IMAGE_TAG` 改为 `knowledge-graph-3d` 并重建了 web；但该 tag 的 **api 镜像从未推送**（仅 web 镜像存在于 173 本地），导致按 compose 重建 worker 会 pull 失败，worker 被继续留在旧版本。
- 修复：不改动 `.env.production`（保留对方 web 部署意图），重建 worker/quality-sync 时用环境变量覆盖：
  `HARNESS_HARBOR_IMAGE_TAG=skill-catalog-20260910 docker compose ... up -d --no-build --no-deps --force-recreate worker quality-sync`
- 验证：4 个容器全部 healthy 且运行 `skill-catalog-20260910`；在原失败会话发送测试消息创建 run，状态 `succeeded`。
- 遗留风险（需与知识图谱工作线协调）：
  1. `.env.production` 当前指向不存在的 `agent-studio-api:knowledge-graph-3d`；任何对 api/worker 的 `compose pull/up` 都会失败，直到该线推送 api 镜像或改回统一 tag。
  2. 正在运行的 web 是本地镜像 `agent-studio-web:knowledge-graph-3d`（未推送 harbor），宿主机清理镜像或跨机迁移会丢失。
  3. 建议后续发布统一走 `scripts/build_harbor_174.sh` 式的"一次 tag、api/web/worker 同批滚动"流程。

## 8. 统一版本：graph-skills-20260910（与 3D 图谱工作线合流）

- 提交基线：`d6ad1f4`（含 3D 图谱全部修复至 `cd2f310`、知识库 answer/RAG 后端、office skill 探测措辞修正）。
- 背景：`knowledge-graph-3d` 仅有本地 web 镜像、env tag 指向不存在的 api 镜像（见第 7 节遗留风险）；本轮以合并后的 HEAD 重建 api+web，全部组件统一滚动。
- 变更：api/web/worker×3/quality-sync 全部运行 `graph-skills-20260910`；env tag 修复为同值（备份 `.env.production.bak-graph-skills-20260910-*`）。
- 附带修复：office skill 的探测指令改为以 `; true` 收尾——此前探测链末尾 `which libreoffice` 退出码 1，把整个工具结果误标为 `is_error`（沙箱无 LibreOffice 属预期，python-pptx 1.0.2 实际可用）。
- 验证：6 容器 healthy；`/v1/studio/skills/catalog` 9 包；运行镜像内确认探测修正已生效；真实 run `succeeded`；web `:3301` 200。

## 9. lead-agent 默认绑定全部平台 Skills（lead-platform-skills-20260910-r2）

- 提交基线：`01160ab`（默认 agent 供应时物化平台 Skill 目录）+ api-update.Dockerfile 增量镜像同步 `agents/lead-agent`。
- 机制：`AgentService._build_default_report` 在 `ensure_user_default` 供应默认 agent 时，把 `default_platform_skill_catalog()` 全部包物化进临时副本（同名目录以平台目录为准覆盖），版本号追加确定性后缀 `+platform.<digest8>`——平台目录更新自动作为新的不可变版本供应，无需手动同步或升版本号；lead 专属 skill（general-task-orchestration、skill-creator）保留。
- 命名澄清：「通用助手」是 lead-agent 的 UI 显示名（09-08 发布设计，内部标识不变）；**老会话固定在创建时的 agent 版本**（不可变原则），本例 PPT 会话固定在 `lead-agent@1.0.0`（仅 1 个 skill），**新建对话**才会使用新版本。
- 验证：用户目录出现 `lead-agent@1.0.2+platform.58a522a1`；该版本快照 `skill_snapshots` 含 11 个 skill（7 个原库 + 4 个办公包）；新会话 run 的 `agent.assets.staged` 事件确认 11 个 skill 全部物化进运行；run `succeeded`。
- 体验备注：直接问模型"你有哪些技能"不可靠（模型无法自省工具列表），以实际任务触发为准；本轮增量镜像顺带把容器内滞留的 lead-agent 1.0.0 包更新为仓库当前 1.0.2。

## 10. 默认会话版本钉修复（HARNESS_AGENT_VERSION）

- 现象：新建对话仍固定 `lead-agent@1.0.0`。根因：Web BFF 以 `HARNESS_AGENT_VERSION`（缺省 1.0.0）为新建会话钉版本，与平台目录/供应无关。
- 修复：173 `.env.production` 设 `HARNESS_AGENT_VERSION=1.0.2+platform.58a522a1` 并重建 web。
- 验证：新会话固定到该版本；run `agent.assets.staged` 物化 11 个 skill；run `succeeded`。
- 已知耦合：平台目录内容变化会生成新的 `+platform.<digest>` 版本号，该环境变量需同步更新（后续可改为 BFF 不钉版本、由后端解析默认部署）。

## 11. Skill 工具缺失根因修复（lead-platform-skills-20260910-r4）

- 现象：版本含 11 个 skill 且 staged 成功，但模型回答 `SKILL_TOOL_MISSING`，直接裸写 python-pptx。
- 排查：`runtime.system` init 事件的 tools 列表只有声明内建工具 + MCP，无 `Skill`。
- 根因：`ClaudeAgentOptions(tools=...)` 的语义是**替换**内置工具基础集。Harness 传入显式清单（Read/Glob/Grep/Write/Edit/Bash/WebSearch/WebFetch），`skills=` 参数只影响权限白名单（`Skill(name)` 加入 allowedTools）与发现源（setting_sources 默认 user,project），但 Skill 工具本身不在基础集里就永远不会注册。
- 修复：运行时构造 options 时，凡 bundle 带 skill 快照即向 `tools` 追加 `"Skill"`（`src/harness/runtime/claude_sdk.py`）；同时 SDK 从基座的 0.2.128 升级到 0.2.152（与 uv.lock 一致，内置 CLI 2.1.259）。
- 验证：init tools 列表含 `Skill`；模型真实调用 `Skill(skill="office-pptx")`（tool.request 事件）；run `succeeded`。
- 覆盖说明：新建对话版本由 `HARNESS_AGENT_VERSION=1.0.2+platform.58a522a1` 钉住（见第 10 节）；老会话仍固定 1.0.0。
