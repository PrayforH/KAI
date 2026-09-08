# 工作空间 Agent 模型与多人协作设计

日期：2026-08-05
状态：设计定稿，三步均已实现并通过验证（见第 9 节实施记录）

## 1. 现状权限模型分析

### 1.1 三层身份边界

当前平台的身份与权限分三层，全部以 `tenant_id`（工作区边界）为前提：

1. **租户成员 RBAC**：`tenant_memberships` 表保存 `owner / admin / member / viewer` 四种角色。
   `api/dependencies.py:985-1021` 的 `_ROLE_PERMISSIONS` 把角色展开为权限字符串
   （`tasks:read/write`、`studio:read/write/publish`、`members:read/write` 等），
   `ensure_permission` 做并集校验，`owner` 持有 `*`。
2. **团队空间 RBAC**：`team_spaces` + `team_space_members` 保存
   `owner / admin / contributor / viewer` 四种空间角色（`sharing/models.py:13-17`）。
   空间是**授权容器**，不拥有资源——它只保存"对个人不可变 Agent 版本的授权引用"。
3. **资源所有者**：`agent_versions`、`agent_drafts`、`mcp_credentials`、`eval_*`、
   `deployment_*` 等资源全部带 `owner_user_id`（迁移 0021 固化），
   私人资源默认仅所有者可见可写。

### 1.2 Agent 身份模型（核心问题）

- `AgentVersion`（`core/models.py:102-111`）主键为
  `(tenant_id, owner_user_id, name, version)`，**没有稳定 agent_id**。
- `AgentDraft`（`studio/models.py:239-255`）主键为
  `(tenant_id, owner_user_id, draft_id)`，`draft_id` 是稳定的，但草稿与版本之间
  没有共享的身份连线——草稿发布后产生同 owner 下的 `name@version` 版本，
  但**后续新草稿、新版本与原身份无关联**。
- 因此"一个 Agent"在当前模型里是隐含的：`owner + name` 的聚合。
  任何 `name@version` 的拼串（前端 key、去重、缓存、URL）都可能在
  两个用户发布同名版本时产生串扰。

### 1.3 共享模型

- `shared_agent_versions`（迁移 0022）保存
  `(tenant_id, space_id, agent_owner_user_id, agent_name, agent_version)` 授权行，
  只允许共享 `PUBLISHED` 的不可变版本（`sharing/service.py:127-155`）。
- 共享不改变所有权：成员运行共享 Agent 时 Session 同时固定
  `user_id`（运行者）与 `agent_owner_user_id`（定义者），
  MCP 凭据按运行者解析（`team-spaces.md` 第 25-36 行）。
- **没有**：共享草稿、共同编辑、版本切换（切换版本意味着换一个 `name@version`
  坐标，身份会变）、Agent 级 ACL、用户组、所有权转移。

### 1.4 前端身份现状

- `task-agent-catalog.ts` 已实现 `agentCoordinate()`（完整坐标
  `scope:spaceId:ownerUserId:name@version`），任务选择器与线程绑定
  （`thread-store.ts` 的 `ThreadAgentBinding`）已携带 ownerUserId/spaceId。
- 剩余正确性问题（详见第 3 节）：
  - `task-agent-catalog.ts:149-156` 最后一步去重仍只按 `name@version`；
  - `task-agent-catalog.ts:93-94` 用 `name@version` 合并 Studio 草稿与注册表，
    Studio 草稿是**个人**的，与共享 Agent 同名同版本会被错误合并；
  - `page.tsx` 的 `AssistantRuntimeShell` React key 使用完整坐标（正确），
    但 `switchAgent`/`switchTask` 的"同 Agent"判断散落在多处且不一致；
  - 空间页（`team-spaces.tsx`）与任务选择器没有显式的 `can_view` / `can_chat` 语义：
    空间页对所有成员展示共享 Agent（应展示），任务选择器由后端在
    `api/routes/agents.py:74-79` 过滤（viewer 且 `runnable_by_viewer=false` 不可见），
    但前端没有统一的权限字段。

### 1.5 运行隔离

- `ExecutionIdentity`（`core/models.py:84-99`）固定 `agent_owner_user_id`、`team_ids`、
  `agent_name + agent_version`；`sessions` 保存同样的快照（`core/models.py:114-135`）。
- Worker 只信 Session 快照，不重新解析身份——这是正确的，迁移时不能破坏。

## 2. 主流做法参考

| 平台 | Agent 身份 | 版本 | 多人编辑 | 权限 |
| --- | --- | --- | --- | --- |
| Dify | App（应用 ID 稳定） | draft/publish 分离，版本列表 | 团队空间内多人编辑草稿 | 空间级 RBAC + 审计 |
| Coze Studio Plus | Bot ID 稳定 | 版本/发布 | 团队空间协作 | 空间 RBAC、SSO、审计 |
| GitHub Agents | Agent 定义 = 仓库文件 | Git 提交/PR | PR review = 共享编辑 + 审批 | 仓库级权限 |
| 本项目现状 | 无稳定 ID，`owner+name@version` 隐含 | 草稿 revision + 不可变版本 | 无共享草稿 | 空间角色 + 版本授权行 |

可复用的结论：

1. **身份必须稳定**：平台（Coze/Dify）用 App/Bot ID，GitHub 用文件路径；共同点是
   "Agent 是谁"与"Agent 现在是哪个版本"解耦。本项目引入工作区拥有的稳定 `agent_id`。
2. **版本是不可变 Release**：草稿是可变工作区，发布产生不可变 Release；
   切换"当前发布版本"不改变身份（本项目 `agent_versions` 已不可变，缺的是
   agent 身份与"当前版本"指针）。
3. **草稿共享 + 乐观锁**：多人编辑同一 Agent 时，草稿是共享工作区；
   revision/ETag 乐观锁防止丢失更新（本项目草稿已有 `expectedRevision` CAS，
   缺 ETag 传输与共享所有权）。
4. **权限细粒度化**：空间角色是粗粒度基线，Agent ACL 支持按 Agent 授权
   （GitHub 的 CODEOWNERS 思路）；用户组解决批量授权。
5. **生命周期要显式处理**：成员退出、创建者离职、Agent 转移（Coze/Dify 都有所有权
   转移能力；GitHub 是仓库 transfer）。

## 3. 实施路线（三步）

### 第一步：修正正确性问题（不改变存储模型）

目标：迁移完成前，前端任何身份、缓存、去重、React key 都不再把
`name@version` 当唯一身份；显式引入 `can_view` / `can_chat` 权限语义。

1. **所有前端身份、缓存、去重和 React key 改为稳定 agentId（临时 = 完整坐标）**：
   - 新库函数 `agentIdentity(agent)`：`agentId` 优先，否则返回完整坐标
     `scope:spaceId:ownerUserId:name@version`（与现有 `agentCoordinate` 一致）；
   - `task-agent-catalog.ts`：修复末尾 `name@version` 去重；
   - Studio 草稿与注册表合并 key 改为完整坐标；
   - `page.tsx`、`task-agent-switcher.tsx`、`thread-store.ts` 的"同 Agent"判断
     统一收敛到 `agentIdentity`。
2. **迁移前临时使用完整坐标**：`scope + spaceId + ownerUserId + name + version`
   已在 `agentCoordinate` 实现，补全剩余散落点。
3. **空间页面按 `can_view` 展示**：`AgentCatalogItem` 增加
   `can_view: bool`（空间成员必为 true）、`can_chat: bool`（viewer 且
   `runnable_by_viewer=false` 时为 false）、`can_edit: bool`（未来共享草稿用）。
   空间页展示共享 Agent 列表不再依赖"运行"语义。
4. **任务选择器按 `can_chat` 展示**：前端过滤 `can_chat === false` 的 Agent，
   后端 `api/routes/agents.py` 保留现有过滤并输出新字段。
5. **不再使用 `name@version` 判断唯一 Agent**：前端全部收敛到 `agentIdentity`；
   后端保持 `(tenant, owner, name, version)` 完整坐标。

### 第二步：升级共享模型（引入稳定 agent_id）

1. **引入工作区拥有的稳定 `agent_id`**：
   - 新表 `agents`：`(tenant_id, agent_id)` 主键；`scope ∈ {personal, workspace}`；
     `owner_user_id`（personal 必填）/ `space_id`（workspace 必填）；
     `name`、`display_name`、`description`、`status`、`current_version`、
     `created_by`、`created_at`、`updated_at`。
   - `agent_versions` 增加 `agent_id` 列并回填：为每个 `(tenant, owner, name)`
     聚合创建个人 Agent；`(tenant, owner, name, version)` 行挂到该 Agent 下。
   - `agent_drafts` 增加 `agent_id` 列：草稿归属 Agent；新建草稿即新建 Agent
     （个人草稿 = 个人 Agent），发布草稿 = 给该 Agent 增加 Release。
2. **当前个人共享版本迁移成工作区 Agent 的 Release**：
   - 新表 `agent_releases` 取代 `shared_agent_versions` 的授权语义：
     `(tenant_id, space_id, agent_id, version)` 主键，附加
     `promoted_by`、`runnable_by_viewer`、`created_at`。
   - 迁移 0023：为空间里每个去重后的 `(owner, name)` 聚合在工作区创建一个
     `scope=workspace` 的 Agent；原 `shared_agent_versions` 行变成该 Agent 的
     Release 行；`fork` 路径改为"以 Release 为源创建个人 Agent"。
   - 共享版本切换：`agents.current_version` 指针（空间内由具备发布权限的成员
     promote），切换不改变 `agent_id`。
3. **引入 Agent ACL**：
   - 新表 `agent_acls`：`(tenant_id, agent_id, grantee_type ∈ {user, group, space_role},
     grantee_id, permission ∈ {view, chat, edit, publish, manage}, granted_by, created_at)`；
   - 工作区 Agent 的基线权限仍从空间角色推导（owner/admin=manage，contributor=edit+publish，
     viewer=view），ACL 行用于显式加授；个人 Agent 默认为 owner 本人 manage。
4. **增加"当前发布版本"**：`agents.current_version`；
   运行解析顺序 `agent_id + current_version → 解析为不可变 Release`。

### 第三步：补齐多人协作

1. **共享草稿和 revision/ETag 乐观锁**：
   - 工作区 Agent 的草稿为共享资源：`agent_drafts` 增加 `space_id`（可空），
     空间成员按 ACL `edit` 权限读写；
   - 草稿 API 返回 `ETag: "revision-{revision}"`，写请求带
     `If-Match` 或 `expectedRevision`（保留现有 CAS，双保险）。
2. **版本历史、回滚、发布审计**：
   - `GET /v1/agents/{agent_id}/versions`（历史列表，含发布人、哈希、时间）；
   - `POST /v1/agents/{agent_id}/versions/{version}/promote`（设置当前版本 = 回滚）；
   - 发布/回滚写入审计（`audit_logs`，action=`agent.publish` / `agent.promote`）。
3. **用户组授权**：
   - 新表 `user_groups`、`group_members`、组级 ACL（`grantee_type=group`）。
4. **caller-owned / service-owned 连接模式**：
   - Agent 或 Release 增加 `connection_mode ∈ {caller_owned, service_owned}`；
   - `caller_owned`（现状）：MCP 凭据按运行者解析；
   - `service_owned`：工作区为 Agent 提供共享凭据引用，成员运行不要求个人配置。
5. **成员退出、创建者离职、Agent 转移的生命周期**：
   - 成员退出：撤销空间成员 → ACL 查询自动失效；新 Session 被拒，历史任务保留；
   - 创建者离职：`POST /v1/agents/{agent_id}/transfer` 转移个人 Agent 所有权
     （personal→personal）或上缴工作区（personal→workspace）；
   - 空间创建者离职：现有"至少保留一位 Owner"约束 + 显式转移接口。

## 4. 目标数据模型（迁移 0023 摘要）

```text
agents (tenant_id, agent_id)                     -- 新增
  scope, owner_user_id?, space_id?, name, display_name, description,
  status(active|archived), current_version?, created_by, created_at, updated_at

agent_versions (tenant_id, owner_user_id, name, version)   -- 既有
  + agent_id NOT NULL                                       -- 0023 新增回填
  + connection_mode(caller_owned|service_owned) 默认 caller_owned

agent_drafts (tenant_id, owner_user_id, draft_id)  -- 既有
  + agent_id NOT NULL                               -- 0023 新增回填
  + space_id?                                       -- 共享草稿

agent_releases (tenant_id, space_id, agent_id, version)  -- 取代 shared_agent_versions
  promoted_by, runnable_by_viewer, created_at
  + connection_mode

agent_acls (tenant_id, agent_id, grantee_type, grantee_id, permission)  -- 新增
user_groups (tenant_id, group_id) / group_members (tenant_id, group_id, user_id) -- 新增
```

兼容规则（沿用 `docs/agent-draft-schema-evolution.md`）：

- 0023 为可逆迁移；`agent_id` 先以可空列添加、回填、再收紧为 NOT NULL；
- 个人 Agent 在 `agents` 表按 `(tenant, owner, name)` 聚合回填，幂等；
- 共享版本迁移为工作区 Agent Release 时，`agents.name` 取原聚合 name；
- 旧客户端（无 agent_id）继续用完整坐标解析，新客户端优先 agent_id。

## 5. API 变更

### 目录与运行

- `GET /v1/agents`：`AgentCatalogItem` 增加
  `agent_id`、`can_view`、`can_chat`、`can_edit`、`connection_mode`、`current_version`；
  personal 与 workspace 两类统一输出。
- `POST /v1/sessions`、`POST /v1/agui`：接受可选 `agent_id`；
  有 `agent_id` 时校验 ACL（chat），无则回退完整坐标（兼容迁移期）。

### 工作区 Agent

- `GET /v1/spaces/{space_id}/agents`：改为返回工作区 Agent + Release 列表
  （`can_view` 展示，`can_chat` 运行）；
- `POST /v1/spaces/{space_id}/agents`：发布个人 Agent Release 到工作区
  （自动创建/复用工作区 Agent）；
- `POST /v1/spaces/{space_id}/agents/{agent_id}/promote`：切换当前发布版本；
- `GET/PUT /v1/spaces/{space_id}/agents/{agent_id}/acl`：Agent ACL 管理；
- `POST /v1/spaces/{space_id}/agents/{agent_id}/fork`：以当前 Release 为源复制到个人。

### Studio

- `GET/PUT /v1/studio/drafts/{draft_id}`：返回/接受 `ETag`（revision）；
- `GET /v1/studio/drafts/{draft_id}/versions`：发布历史；
- 工作区共享草稿：`GET /v1/studio/drafts?scope=workspace&space_id=...`。

### 生命周期

- `POST /v1/agents/{agent_id}/transfer`：个人 Agent 所有权转移/上缴工作区；
- `GET/POST/DELETE /v1/groups`：用户组管理。

## 6. 前端变更

- `TaskAgent` 增加 `agentId`、`canView`、`canChat`、`canEdit`、`connectionMode`；
- 新 `agentIdentity()` 统一身份/去重/key；`groupTaskAgents` 按
  `agentId ?? 完整坐标` 分组；
- 任务选择器过滤 `canChat === false`；
- 空间页按 `canView` 展示共享 Agent，显示"当前发布版本"，提供 promote 与 ACL 管理
  （owner/admin）；
- Studio 工作台支持工作区共享草稿（`canEdit` 控制）与 ETag 冲突提示。

## 7. 测试与验证

- 后端：`tests/unit/sharing/`（Release 迁移、ACL、promote、transfer）、
  `tests/unit/studio/`（ETag、agent_id 发布）、`tests/integration/api/`（新端点）；
- 迁移：0023 upgrade/downgrade 往返 + 既有数据回填样例；
- 前端：`task-agent-catalog.spec.ts`（去重修复、canChat 过滤）、
  `agent-studio.spec.ts`（ETag）、`task-agent-switcher.spec.ts`（agentId 分组）；
- 门禁：`make verify`（ruff + pyright + pytest）、`make web-test`、`make e2e`。

## 8. 分步交付

| 步骤 | 交付物 | 验收 |
| --- | --- | --- |
| 1 | 前端身份收敛 + can_view/can_chat 字段 | 前端测试通过；同名不同 owner 不再串扰 |
| 2 | agents/agent_releases/agent_acls + 迁移 0023 + API | 后端测试通过；迁移往返可逆 |
| 3 | 共享草稿 ETag、版本历史/promote、用户组、连接模式、生命周期 | 后端测试通过；e2e 通过 |

## 9. 实施记录（分支 feat/workspace-agent-model）

### 第一步（已提交 532d67f）

- `task-agent-catalog.ts` 新增 `agentIdentity`（同 Agent 判定）与 `agentItemKey`
  （版本敏感的去重/React key）；修复按 `name@version` 去重导致的跨用户串扰；
  Studio 草稿与注册表合并改为按 owner 匹配。
- `AgentCatalogItem` 增加 `agent_id/current_version/connection_mode/can_view/
  can_chat/can_edit`；目录与空间 API 输出权限投影。
- 任务选择器按 `canChat` 过滤；空间页展示共享 Agent。

### 第二步（已提交 4041839）

- 迁移 0023：`workspace_agents`（稳定 agent_id，personal/workspace 两种 scope）、
  `agent_releases`、`agent_acls`；为 `agent_versions/agent_drafts` 回填 `agent_id`；
  把存量 `shared_agent_versions` 授权迁移为工作区 Agent + Release，
  新 Release 成为当前发布版本。upgrade/downgrade/upgrade 已在真实 PostgreSQL 上
  验证幂等。
- 发布链路：`AgentService`/`AgentStudioService` 通过 `AgentIdentityService`
  分配个人 Agent 身份，草稿与版本共享 `agent_id`。
- 共享模型：`share_agent` 创建/复用工作区 Agent 并追加 Release；
  `promote_release` 切换 `current_version` 不改变身份；`unshare` 清理当前版本；
  `fork` 以当前 Release 为源；运行门禁 `require_agent_access` 经 Release 解析。
- Agent ACL：空间角色基线 + 显式 ACL 行（user/space_role 主体）；
  viewer 的 CHAT 需 `runnable_by_viewer` 或显式授权。

### 第三步（本提交）

- 共享草稿乐观锁：草稿 GET/PUT 返回 `ETag: "rev-N"`，PUT 支持 `If-Match`
  （412 冲突），与 `expectedRevision` CAS 双保险。
- 工作区共享草稿（本提交补齐）：`agent_drafts.space_id` 启用——
  `POST /v1/studio/drafts` 带 `agentId+spaceId` 创建共享草稿（EDIT 要求，
  名称必须等于工作区 Agent 身份）；空间成员按 `AgentPermission.EDIT`
  （角色基线 + 用户/组 ACL）读写，VIEWER 只读；`GET /drafts?spaceId=` 列出
  空间草稿；共享草稿 publish 需 PUBLISH 权限，发布后自动作为 Release
  发布并 promote 为当前版本；草稿名不可变更（身份即名称）。
- 发布审计：`agent.share/agent.promote/agent.transfer` 写入 audit_logs。
- 用户组：迁移 0024 新增 `user_groups/group_members`；`/v1/groups` CRUD API；
  ACL 支持 `group` 主体（组内任一成员继承授权，移出组即撤销）。
- 连接模式：`AgentRelease.connection_mode`（caller_owned/service_owned）经
  Session 快照贯穿运行链路（sessions API 与 AG-UI 均固定到 Session），并
  透传到 `ExecutionIdentity`（runtime 与 worker 构造处）。
- service_owned 运行时凭据解析（本提交补齐）：Worker 凭据解析在
  `StoredMcpCredentialProvider` 按 `connection_mode` 分流——service_owned
  时按 `team_ids[0]` 解析 `space:{space_id}` 空间级凭据（`mcp_credentials`
  表 owner 命名空间复用，无需迁移），**绝不回退到调用者个人凭据**；
  空间凭据由 `PUT/DELETE /v1/spaces/{space_id}/mcp/{reference}/credentials`
  管理（Owner/Admin）。
- 生命周期：`POST /v1/agents/{agent_id}/transfer` 支持 personal→personal
  （版本与草稿整体 re-key，`agent_id` 不变）与 personal→workspace 上缴；
  成员退出/撤权 fail-closed 已有测试覆盖。

### 验证结论

- 后端：新增共享草稿集成测试（成员读写/权限矩阵/组 ACL/发布 promote/非成员
  404）与 service_owned 凭据解析测试（空间凭据优先、个人凭据不泄漏、删除后
  fail closed）；完整套件 884+ 通过，仅剩既有/环境失败。
- 前端：297/298 通过（唯一失败 workbench-layout 为既有问题）；`next build` 通过。
- 前端：297/298 通过（唯一失败 workbench-layout 为既有问题）；`next build` 通过。
- 剩余失败均为环境/既有问题（Redis/MinIO/知识库搜索/配额用例），在干净分支
  上同样失败，与本次改动无关。
