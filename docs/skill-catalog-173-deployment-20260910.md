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
