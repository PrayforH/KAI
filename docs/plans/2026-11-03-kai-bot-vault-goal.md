# KAI Bot 交互与 Vault 收敛（至1103 0830）· 检查点

> 合同：`/Users/xiaokai/.codex/visualizations/2026/11/02/kai-superagent-plan/zcode-bot-vault-20261103-brief.md`
> 飞书第10章（现行修订）/ 第11章（协同计划）/ 11.6（追加）
> 结果：`docs/results/2026-11-03-kai-bot-vault-79.md`

## 0. 真实起点

| 项 | 事实 |
| --- | --- |
| 独立工作树 | `/Users/xiaokai/.codex/worktrees/kai-bot-20261103/agent studio`（本文件所在） |
| 分支 | `auto/kai-bot-vault-20261103`，基线 `60ef9143`（代码 `0db1e0b7`） |
| 未触碰 | `auto/kai-assistant-managed-20261030`（旧首段任务 `kai-assistant-early-public-progress` 正在跑）；主目录 `/Users/xiaokai/Documents/agent studio` 的他人改动 |
| schema | `0064`（head，本任务如需新迁移从 `0065` 起） |
| 79 现网 | API/Worker/Web = `20261102-0db1e0b7`（部署前重新核对） |
| 截止 | 北京时间 2026-11-03 08:30；08:00 收敛安全小切片，08:15 整理证据，08:30 不启动新工作 |

## 1. P00 基线与既有能力（只读核实，不重做）

### P13 相关

- 助理实体：`assistant_specialists`（`src/harness/assistant/storage.py:149`）。**除 `name/description` 外全部在 `payload` JSON 里**：`config["appearance"] = {color, avatar}`、`config["title"]`、`config["sidebar"]`、`config["credential_ref:*"]`。
- 资料与配置是两个端点：
  - `POST /v1/studio/assistant/specialists/{id}/profile` → `name/title/description/appearance/sidebar`（`api.py:360`）
  - `POST /v1/studio/assistant/specialists/{id}/config` → 执行配置（权限/计划/工具，`api.py:377`）
- 调色板目前**只在前端**：`web/harness-console/src/lib/bot-appearance.ts`（12 色 + 14 形 SVG path）。服务端只有正则校验（hex / slug），没有白名单。
- 新建时前端把外观随 `POST /specialists` 一起提交；**服务端没有随机分配逻辑**（同名 slug 复用既有实例时会丢弃新外观）。
- 顶栏在 `src/app/page.tsx:950-969`：头像 `AssistantHeaderAvatar` + 名称 + 角色副标题 `专职子助理` + 「…」更多按钮。
- 编辑弹窗 `src/components/bot-edit-dialog.tsx` 把资料、外观、例行程序、启用/暂停、试运行、本地能力授权、凭据**混在一个弹窗**里 → 与 P13「资料表单不混入权限/计划/工具配置」冲突。
- 白色外观：`BOT_COLORS[0] === "#ffffff"`，当前 glyph 直接填白、无底板、眼睛固定 `rgba(255,255,255,.92)` → 白底白字不可读。

### P14 相关

- `@` 现在**只指向知识库**（`src/components/composer-assist.tsx:18-28`，`composerTrigger` 识别 `/ @ $`），后端**零** mention 解析。
- 没有「给某个 Bot 发消息」的 HTTP 端点；现存唯一通路是 `POST /v1/agui` 带 `threadId`（前端 `src/lib/thread-answer.ts:55` 的 `submitThreadAnswer`）。
- 可复用：`AssistantService.resolve_targets`（`service.py:449`，名字→真实 thread）、`GET /specialists/{id}` 返回 `threadId`、`SpecialistHandoverRow.message_id` 已是幂等来源键、`handoverStore` + `HandoverInlineRows` 已渲染「已发送消息给 X」、`AssistantEventLine` 是收/发共用的行渲染器。
- 幂等：`Idempotency-Key` 头 → `trigger_key=manual:<key>`；objective `idempotency_key` 唯一索引；handover `uq_assistant_handover_message`（tenant,user,message_id）。

### P15 相关

- **没有 Vault**（表/服务/路由/UI 都不存在）。可复用底座：
  - `mcp_credentials` 表 + `McpCredentialCipher`（AES-GCM，AAD = tenant\0owner\0reference）+ `McpCredentialService`（`src/harness/studio/mcp_credential_store.py`），列表只回脱敏元数据 `McpCredentialStatus`。
  - `assistant_specialist_credentials` 表 + `credential_ref:<name>` 引用模式（**只写不读**，没有 list/get/delete）。
- 卡片 / CVV / 支付概念**全仓不存在**（唯一命中是 `memory_bank/safety.py:26` 的银行卡脱敏正则）。
- 删除确认范式：`src/components/confirmation-dialog.tsx` 的 `useConfirmationDialog()`。
- 设置页是单文件 hash 路由：`src/app/settings/page.tsx`（`SETTINGS_NAV` 注册分区；`/settings/memory/page.tsx` 是重定向 shim）。
- 密钥不进模型/日志的既有闸门：`runtime/audit_redaction.py`、`observability/redaction.py`、`runtime/mcp_credentials.py`；`CredentialLease.values` 是 `exclude=True`。

### 其它

- `web/codex-web` 与助理/外观**无关**（只有 Codex 主题的 appearance 设置）；其 `AGENTS.md` 只约束 codex-web。
- 普通任务 / 项目任务与助理面共用 composer 与 `agent-thread.tsx`，隔离靠 `conversationSurface`（`app/page.tsx:273-276`）与 `useAssistantSurface()`。**P14 只能在 `assistantSurface` 上启用**，不得改变任务面行为。

## 2. 交付切片（本轮）

| 包 | 内容 | 状态 |
| --- | --- | --- |
| P13-a | 助理会话页头只留名称；点名称进「姓名/标题/描述」三项资料弹窗；权限/计划/工具留在原处 | ✅ 实现 + 本机实页验收（`p13-header-name-only.png`） |
| P13-b | 服务端随机持久外观：未用色/形优先 → 未用组合 → 低重复；创建后固化；幂等/并发/刷新稳定；不改已有或手选 | ✅ 实现 + 单测 + 实页（`p13-random-appearance.png`） |
| P13-c | 白色外观中性灰底 + 深浅主题足够对比度；统一 appearance token，不各处硬编码 | ✅ 实现 + 单测（≥4.5:1）+ 实页（`p13c-white-appearance.png`） |
| P14 | 输入 `@` 弹可访问助理 → 过滤 → 键盘选择 → 稳定 ID mention；只选中不提交；发送才发/派工；回执「已发送消息给」 | ✅ 实现 + 单测 + 实页（`p14-mention-receipt.png`） |
| P15 | 设置内助理 Vault：全部/登录密钥/卡片/联系信息分类；搜索、新建、编辑、受控使用、明确删除确认；复用加密存储与 credential_ref | ✅ 实现 + 单测 + 实页（`p15-vault-item.png`） |
| ADD-1 | 状态行：助理图标 + 短真实阶段短语 + 扫光，无句末标点；不再只有「已开始处理，正在准备回复。」 | ✅ 实现 + 单测（实页未取到运行中状态行） |
| ADD-2 | 助理默认跟随平台联网（复用 WebConfigurationService），新建默认继承 | ✅ 已实现（`assistant_scope` 运行时注入）+ 79 实测发现真缺口（子助理的已发布工具目录无联网工具）+ 集成测试覆盖；决定性用例仍是测试级证据 |
| P10 | 仅 79 渐进发布 + 真实网页验收 | ⚠️ **79 已发布**（api/worker `20261103-0025afa8`、web `20261103-67ef9304`，schema 0065，三服务 healthy）；**79 真实接口验收通过**；**79 实页验收未做**（自签证书 + `AUTH_COOKIE_SECURE=true`，需本人接管，见结果文档 8.5） |
| P11 | 飞书同步 + `docs/results/2026-11-03-kai-bot-vault-79.md` | ✅ 结果文档与检查点齐备；飞书已同步 revision 83／84／85（逐次回读确认，未覆盖历史内容） |

## 3. 阶段记录

- **00:15** 读完合同与仓库指令；建立独立工作树（`auto/kai-bot-vault-20261103`，基线 `60ef9143`）；P00 三份只读事实图完成。
- **01:15** P13 落地（页头三项资料 + 服务端随机外观 + 统一 appearance token + 状态行图标/短语）：`08ceba23`。
- **02:30** P14 落地（@ 提及 + 幂等回执 + 居中回执行）：`e5916955`。
- **03:50** P15 落地（设置内分类 Vault + 加密存储 + 按 Bot 授权 + 删除确认 + 迁移 0065）：`40e8f0df`。
- **04:30** 发现并修掉既有缺陷：`_default_agent` 调用不存在的 `AgentService.list_for_user`，导致主助理资料记录永远建不出来（基线同样存在）。
- **05:30–07:30** 本机真实页面验收（生产容器 + 全新 Postgres 库 + 全新账号）：页头/随机外观/@ 提及与回执/Vault/白色外观五项均取得截图证据。
- **07:35** 结果文档 `docs/results/2026-11-03-kai-bot-vault-79.md` 落盘；79 发布与 79 实页验收因缺凭据明确标为未完成。
- **07:50** 飞书同步：追加本轮进展，回读 revision 83（历史内容未被覆盖）。
- **07:55** 79 发布脚本备好：`/Users/xiaokai/.codex/visualizations/2026/11/02/kai-superagent-plan/deploy-kai-bot-79.sh`。
- **01:16**（次日）监督者用用户既有凭据重建 79 的 SSH ControlMaster（`/tmp/kai-zcode-1103.sock`）→ 部署阻塞解除。
- **01:20–01:50** 合并旧首段任务提交 `842adcfd`/`ef9710a6`（`67ef9304`）；补齐 deepagents extra 后后端单测 2341 通过（1 条既有 VEX 失败与基线同）。
- **02:30–02:55** ADD-2 实现 + 集成测试（`8546636d`）。
- **03:10** 79 第一批发布（迁移 0065，api/worker/web 切换，healthy）。
- **03:40** 79 真实接口验收发现「撤销授权没真正移除 credential_ref」→ 修复（`0025afa8`）→ 第二批发布并复验通过。
- **05:30** 79 真实接口验收全项通过；飞书同步 revision 84。
- **05:45** 本人答复：79 实页验收保留为「需本人处理证书与正常登录」，不授权加证书信任/关校验/伪造登录/改认证配置，也不得用接口验收冒充实页；期间继续可独立完成的工作。
- **06:00–06:30** 对照合同补齐 P14/P15 自己漏掉的覆盖项：停用/归档 Bot 拒收消息、删除资料即收回授权、多目标投递互不阻断（`12a3d523`）。
- **06:30** 第三批发布到 79 并复验通过（三批均 healthy）。
- **06:45** 飞书同步 revision 85（第三批发布、P01—P09 状态、需本人处理的 79 实页项）。P01—P09 的状态投影、隔离、统一输入、执行事件、检查点、成果、预算/云电脑、项目托管在代码与页面上均已具备，本轮未回归重做；仍未达标的是 P03 首段时延（属旧任务范围）与 79 实页项。
- **06:55** ADD-1 数据侧核实：79 真实运行的活动流含 `tool.request(WebSearch/WebFetch)`，前端据此投影真实阶段短语，不再只有固定的「已开始处理，正在准备回复。」；短语的映射由前端单测覆盖。
- **07:00** 收尾：结果文档、检查点、飞书（rev 83/84/85）齐备；79 实页项单列并交本人处理。
- **07:15** 第四批：收回授权时容忍并发编辑（`24212a54`）→ 79 滚动并复验通过。
- **07:25** 飞书同步 revision 86（四批发布与最终版本）。

## 4. 与合同的差异说明

- 真实页面验收在**本机**完成（同一提交），不是 79。79 上未发布、未升级 schema、未做实页验收——已在结果文档「未完成与阻塞」逐条写明，不当作已完成。
- P14 的回传边界、ADD-2 的联网装配、P15 的自动填充均按合同「如实标未支持」处理。

