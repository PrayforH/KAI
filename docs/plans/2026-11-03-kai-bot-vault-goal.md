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
| P15 | 设置内助理 Vault：全部/登录密钥/卡片/联系信息分类；搜索、新建、编辑、受控使用、明确删除确认；复用加密存储与 credential_ref | ✅ 实现 + 单测 + 实页（`p15-vault-item.png`）；**补齐**：凭据按授权在进程内解析（79 实测），撤销/删除即拒用，旧引用给出明确原因；**四层如实区分**：①已保存 ②已授权 ③按授权解析（进程内）④已接工具=**未支持**（运行时无工具消费 `credential_ref`，原因见结果文档 8.9c） |
| ADD-1 | 状态行：助理图标 + 短真实阶段短语 + 扫光，无句末标点；不再只有「已开始处理，正在准备回复。」 | ✅ 实现 + 单测（实页未取到运行中状态行） |
| ADD-2 | 助理默认跟随平台联网（复用 WebConfigurationService），新建默认继承 | ✅ 已实现（`assistant_scope` 运行时注入）+ 79 实测发现真缺口（子助理的已发布工具目录无联网工具）+ **79 实跑证明**：不带联网工具的助理实际调用了 WebSearch/WebFetch 并给出带来源的回答（运行 `run_620bba5b…`，succeeded） |
| P10 | 仅 79 渐进发布 + 真实网页验收 | ⚠️ **79 已发布**（**六批**；**唯一当前版本** api/worker `kai/superagent-api:20261103-e27f8e46`、web `kai/superagent-web:20261103-e27f8e46`，schema 0065，三服务 healthy；第四批 `24212a54` 已被后续批次取代，只是历史）；**79 真实接口验收通过**（含一次真实 WebSearch/WebFetch 运行，以及第六批的改名/撤销/列表断言）；**仅 79 实页验收未做**（自签证书 + `AUTH_COOKIE_SECURE=true`，需本人接管，见结果文档 8.9） |
| P11 | 飞书同步 + `docs/results/2026-11-03-kai-bot-vault-79.md` | ✅ 结果文档与检查点齐备；飞书已同步 revision 83／84／85／86／87（逐次回读确认，未覆盖历史内容），rev 88 追加本轮三项更正 |

## 3. 阶段记录（北京时间，时间取自 git 提交时间与 79 上的文件/构建时间戳）

> 说明：早先版本把本节的时间写成 06:00—07:25，与实际不符，已按可核实来源更正。
> 依据：`git log --date=format:'%m-%d %H:%M'`（本分支提交）与 79 上
> `/data/kai-bot-vault-20261103-67ef9304/` 的构建与 rollout 文件时间（+0800）。
> 监督者消息与本人答复分开记：监督心跳只作为观察，本人的答复才改变执行方式。

| 时间 | 事件 | 依据 |
| --- | --- | --- |
| 23:49（11-02） | 读完合同与仓库指令；建立独立工作树（基线 `60ef9143`） | 提交 `5c85393c` 前的分支起点 |
| 00:19 | P13 落地（页头三项资料 / 服务端随机外观 / 统一 appearance token） | 提交 `08ceba23` |
| 00:28 | P14 落地（@ 提及 + 幂等回执 + 居中回执行） | 提交 `e5916955` |
| 00:35 | P15 落地（设置内分类 Vault + 加密存储 + 按 Bot 授权 + 迁移 0065） | 提交 `40e8f0df` |
| 00:37 / 00:51 | **旧首段任务**的提交（`842adcfd` 模型先说话、`ef9710a6` 发布记录）——其工作树未被本轮触碰 | 对方分支提交时间 |
| 01:04 | 修掉既有缺陷：`_default_agent` 调用不存在的 `AgentService.list_for_user`，主助理资料记录永远建不出来 | 提交 `6f6efef1` |
| 01:07 | 飞书同步 rev 83；发布脚本备好 | 提交 `5c85393c` |
| 01:17 | 合并旧首段任务提交（无冲突，不覆盖对方提交） | 提交 `67ef9304` |
| 01:21—01:34 | **79 第一批发布**：api/worker `…-8546636d`、web `…-67ef9304`，迁移 0064→0065 | 79 `api-build.log` 01:21:54、`web-build.log` 01:24:33、`rollout.log` 01:34:11 |
| 01:32 | ADD-2 实现（`assistant_scope` 运行时注入） | 提交 `8546636d` |
| 01:34—01:45 | 79 真实接口验收 → 查出「撤销授权没真正移除 `credential_ref`」 | 79 `release-result.json` 01:34:25 |
| 01:45 | 修复撤销语义（仓储 config 为合并语义，撤销须显式移除键） | 提交 `0025afa8` |
| 01:45 | **79 第二批发布** | 79 `rollout2.log` 01:45:41 |
| 02:00 | 补齐 P14/P15 自己漏掉的覆盖项：停用 Bot 拒收、删除资料即收回授权、多目标互不阻断 | 提交 `12a3d523` |
| 02:03 | **79 第三批发布**（api + web） | 79 `rollout3.log` 02:03:20 |
| 02:07 | 收回授权容忍并发编辑 | 提交 `5bc6f0de` / `24212a54` |
| 02:08 | **79 第四批发布**（最终版本） | 79 `rollout4.log` 02:08:01 |
| 02:09—02:10 | 结果文档与检查点；飞书 rev 84/85/86 | 提交 `fae4e37e` / `7251c0d4` |
| 02:12 | **本人答复**：79 实页验收保留为需本人处理证书与正常登录；不授权加证书信任/关校验/伪造登录/改认证配置；期间继续可独立完成的工作 | 本人答复（区别于监督心跳） |
| 02:22 | **ADD-2 决定性用例在 79 跑成真实运行**：不带联网工具的助理实际调用 WebSearch/WebFetch 并给出带来源的回答 | 运行 `run_620bba5b22db4ef19004635a3bcb23e1`；页面取回时间 18:22 UTC |
| 02:24 | 修正本节与结果文档中过时结论与错位时间 | 本次提交 |
| 02:29 | 凭据按授权解析（进程内）+ 补齐 P14/P15 尚无证据的覆盖项 | 提交 `c1e17e4d` |
| 02:33 | P14/P15 逐条补证与 ADD-2 决定性用例的记录 | 提交 `3bb71519` |
| 02:34 | 合并旧首段任务分支（无冲突，不覆盖对方提交） | 提交 `450341e6` |
| 02:36—02:39 | **79 第五批发布**（合并对方提交后重发） | 79 `rollout6.log` 02:39；提交 `b68e88eb` |
| 02:42 | 第五批后的状态与凭据按授权解析的实测证据 | 提交 `84be4e28` |
| 03:03 | 改名要搬动授权引用；资料库四层如实区分（第④层「已接工具」标为未支持）；记录「曾临时复制测试智能体版本」的事实与后续规则 | 提交 `e27f8e46` |
| 03:06 | **79 第六批发布**（当前唯一版本 `e27f8e46`） | 79 `rollout7.log` 03:06 |
| 03:07—03:08 | 第六批 79 实测：改名 → 授权键一起搬走；按新名称撤销真的撤掉；列表只给授权状态不给值 | 79 容器内脚本断言全通过 |
| 03:09 | 文档更正：第四批标为历史、给出唯一当前版本、补四层区分与过程事实 | 本次提交 |
| 08:2x | **本人反馈**：@ 提及要像普通聊天一样写在文本行里、可以多个 → 改为行内插入 `@名字`（提及从输入文本解析，删字即取消提及；选中即插入、多个并存；部分失败时已送达的从文本里拿掉、失败的留在行里可重试），chip 栏删除。提交 `de10b7c5`，vitest 1609 过（仍是基线同一组 7 个既有失败）、tsc 通过 | 本人消息；提交与测试输出 |
| 08:2x | ⚠️ **79 第七批（web `de10b7c5`）待发布**：SSH ControlMaster `/tmp/kai-zcode-1103.sock` 已失效（socket 不存在、BatchMode 被拒），无法连 79 构建/滚动；等连接恢复后再发，api/worker 仍停留在 `e27f8e46` 不受影响 | ssh -O check 输出 |

## 4. 与合同的差异说明

- 真实页面验收在**本机**完成（同一提交）；79 已发布并通过**真实接口验收**（含一次真实 WebSearch/WebFetch 运行），但 79 上的**实页**验收仍被自签证书与 Secure Cookie 挡住，已单列并等本人接管，不当作已完成。
- P14 的回传边界、ADD-2 的联网装配、P15 的自动填充均按合同「如实标未支持」处理。
- **P15 的四层区分（结果文档 8.9a）**：①已保存 ②已授权 ③按授权解析（进程内）④已接工具=**未支持**。
  第④层未实现的核实依据：全仓检索 `resolve_specialist_credential` 只有 5 处命中——定义、本轮新增的**列表授权状态判断**
  （只判定 ok/missing/unresolved，不外传值）、3 条单测，**运行时没有任何工具读取 `credential_ref`**；
  既有 `CredentialLease`/`InMemoryCredentialBroker` 服务的是**部署级** MCP 服务器凭据，不是「按用户/按 Bot」，
  复用它等于新造第二套凭据源，与 P15「不新造秘密存储」相悖；而把值交给模型或暴露读秘密的 HTTP 接口都越过合同红线。
  本轮因此**不接**，只如实标注范围。
- **79 现网唯一版本**是第六批 `kai/superagent-api:20261103-e27f8e46` / `kai/superagent-web:20261103-e27f8e46`（schema 0065，三服务 healthy）。
  早先记录里把第四批 `24212a54` 写成「最终状态」的说法**只是历史**，已在本文件与结果文档中标为历史。
- **过程事实**：早期为验证 ADD-2 曾把一个测试智能体版本**临时复制**到合成账户（非正常产品 API 路径），该复制**已删除**，
  本人账户的版本列表未被动过。此后**只用正常产品 API / 正常授权**准备验收数据，不直接改内部表、不扩大权限；
  遇到 403（如 `agents:publish`）如实记为授权边界，不绕过。

