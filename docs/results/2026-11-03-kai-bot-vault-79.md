# KAI Bot 交互与 Vault 收敛（至1103 0830）· 结果

- 工作树：`/Users/xiaokai/.codex/worktrees/kai-bot-20261103/agent studio`
- 分支：`auto/kai-bot-vault-20261103`（基线 `60ef9143`，代码 `0db1e0b7`）
- 提交：`08ceba23`（P13）、`e5916955`（P14）、`40e8f0df`（P15）、`<本文件提交>`（验收与缺陷修复）
- schema：新增迁移 `0065_assistant_vault_items`
- 79 发布：**未执行**（本机无 79 root 凭据，见「未完成与阻塞」；脚本已备好）
- 飞书同步：已追加本轮进展，回读 revision 83（文档 XV4ndItNmoONoIxQPWgcIyLbnJe），未覆盖历史内容
- 真实页面验收：在**本机新代码**（API + Web，同一提交）完成，截图见本目录

## 1. P13 助理页头 / 三项资料 / 随机持久外观

| 要求 | 实现 | 证据 |
| --- | --- | --- |
| 页头只显示名称，无头像、角色副标题、更多按钮 | `src/app/page.tsx` 助理页头只渲染一个名称按钮；`AssistantHeaderAvatar` 已移除 | `2026-11-03-kai-bot-vault-79/p13-header-name-only.png`（真实页面，页头只有「验收主助理」） |
| 点名称直接编辑，仅姓名/标题/描述 | 新增 `src/components/bot-profile-dialog.tsx`（只 3 个字段 + 保存）；权限/计划/工具仍留在侧栏「…」的编辑弹窗 | 真实页面：弹窗字段恰为「姓名/标题（可选）/描述」 |
| 主助理与子助理同一套资料逻辑 | 资料端点 `/specialists/{id}/profile` 对主助理同样生效（同一 `Specialist` 记录） | 真实页面：主助理名称由「主助理」改为「验收主助理」，侧栏同步 |
| 新 Bot 服务端随机外观，未用优先、一次固化 | 新增 `src/harness/assistant/appearance.py`（服务端唯一调色板 + `choose_appearance`）；`create_specialist` 未指定外观时随机分配并写入 `config.appearance` | 单元测试 `tests/unit/assistant/test_appearance.py`（6 条）；真实页面：新建弹窗显示「外观由服务端随机分配」，建后得到 `{"avatar":"leaf","color":"#c82779"}`（与主助理的蓝不同） |
| 幂等/并发/刷新稳定，不重置已有或手选外观 | 同名 slug 复用既有实例不重算；手选外观原样保留 | `test_create_reuses_an_existing_bot_without_touching_its_appearance` 等 |
| 取色表单一来源 | 前端不再自带颜色表，改由 `GET /specialists/appearance/palette` 下发；Python 单测读前端文件断言形状 id 一致 | `test_client_and_server_share_one_palette` |
| 白色外观中性灰底 + 深浅主题对比度 | 统一 appearance token（`--kai-bot-accent/surface/ink/text/bubble`）；浅色本体坐中性灰底板、眼睛深墨；用户气泡不再白底白字 | `p13c-white-appearance.png`：白色 Bot 在侧栏是灰底白形深眼；`tests/bot-appearance.spec.ts` 断言 ≥4.5:1 |
| 状态行恢复 Bot 图标 + 短真实阶段 + 扫光、无句末标点 | `GrokActivityRow` 渲染 `GrokFace` 并用 `statusPhrase` 去句末标点；固定「已开始处理，正在准备回复。」改为「正在思考」 | `tests/assistant-activity-steps.spec.tsx`、`tests/grok-activity*.spec.tsx` |

## 2. P14 @ 具体助理发消息

| 要求 | 实现 | 证据 |
| --- | --- | --- |
| @ 弹出可访问助理、按名字过滤、键盘选择 | `composerOptions` 的 `@` 分支先列助理（稳定 ID `@assistant:<id>`）；↑↓/Enter/Esc 已在既有键盘逻辑内 | 真实页面：输入 `@` 弹出「@验收 Bot / 子助理 · 回车提及」；Enter 选中后出现可删除的提及 chip |
| 只选中不提交 | 选中只写状态（chip），不发送 | 真实页面：选中后输入框清空、发送键未触发 |
| 点发送才投递，走既有通道 | `POST /specialists/{id}/messages` 只登记幂等回执；真正投递复用既有 AG-UI 入口（目标 Bot 的原会话） | 真实页面：发送后侧栏任务区出现该消息；`tests/unit/assistant/test_mention_message.py`（6 条） |
| 不默认新建定时任务 | 投递只进已有会话，不创建 objective/schedule | 实现上未触碰 `delegate_to_specialist` 的职责创建路径 |
| 回执「已发送消息给 · 头像 · 名称」，点名称去目标会话 | 复用既有 handover 与 `AssistantEventLine` 居中行 | 真实页面：`p14-mention-receipt.png` |
| 幂等、重复提交不重复回执 | 同一把 `Idempotency-Key` 只留一条 handover | `test_mention_with_the_same_key_never_duplicates_the_receipt` |
| 任务/项目交互隔离 | @ 候选只在助理面启用；任务面仍是 @ 知识库 | `composer-mentions.spec.ts`、`composer-interactions.spec.ts` 未变 |

## 3. P15 设置内分类 Vault

| 要求 | 实现 | 证据 |
| --- | --- | --- |
| 设置内专用 Vault，四类分类 | `src/components/assistant-vault.tsx` + 设置页新分区「助理资料库」 | 真实页面：`p15-vault-item.png`（全部/登录密钥/卡片/联系信息 + 搜索 + 新建资料） |
| 搜索、新建、编辑、明确删除确认 | 搜索按标题/分类过滤；删除走 `useConfirmationDialog` 且服务端要求标题一致 | `tests/assistant-vault.spec.tsx`（4 条）；删除先弹确认框，未确认不发请求 |
| 复用已有加密存储与 credential_ref，不新造秘密存储 | 值进既有 `assistant_specialist_credentials` 加密表；条目元数据在新表 `assistant_vault_items` | `tests/unit/assistant/test_vault.py`：密文不含明文、条目投影只有元数据字段 |
| 资料归本人、按 Bot/用途授权、默认不共享 | 授权写 `credential_ref:<标题>`，撤销即移除 | `test_granting_writes_a_credential_ref_and_revoking_removes_it` |
| 列表只脱敏元数据 | 列表只有标题/分类/末四位/是否已保存 | 真实页面：页面正文不含所填密钥 |
| 敏感值不进模型/聊天/RunEvent/日志 | 值只在 `cipher.encrypt` 处落地；条目投影与接口都不含值；`credential_ref` 本就不进 Bot 指令 | 单元测试断言 + 既有 `specialist_instructions` 不含 config 的既有测试 |
| 卡片只存安全令牌/末四位，不存 CVV、不做付款 | 卡片分类没有 CVV 字段（传入即 422）；UI 明写「不保存 CVV，也不做付款」 | `test_card_category_has_no_cvv_field` |
| 无安全采集/填充基础时如实标未支持 | UI 只提供令牌/末四位存储，未声称自动填充 | 实现说明与 UI 文案 |

## 4. 本轮修掉的既有缺陷（非本轮引入，但挡住 P13）

- `AssistantService._default_agent` 调用了不存在的 `AgentService.list_for_user` → `POST /specialists/assistant/primary` 500 → **主助理永远无法创建资料记录**，页头名称永远是「我的助理」。已改为 `list_published`（`src/harness/assistant/service.py`）。该缺陷在基线 `60ef9143` 上同样存在。

## 5. 测试与静态检查

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 后端单测（助理） | `uv run python -m pytest tests/unit/assistant -q` | 177 passed |
| 后端新增单测 | `pytest tests/unit/assistant/test_appearance.py tests/unit/assistant/test_mention_message.py tests/unit/assistant/test_vault.py` | 18 passed |
| 后端整树收集 | `uv run python -m pytest tests/unit -q` | 3 个收集错误（`test_lifecycle.py`/`test_memory_tools.py`/`test_service.py`）**与基线完全相同**（同名测试文件的老问题，基线工作树同样报这 3 个） |
| 前端全量 | `cd web/harness-console && npx vitest run` | 1599 passed / 7 failed —— 7 条失败与基线**同一组**（account-settings 1、agent-thread-config 2、assistant-message-controls 1、bot-rail-paused-badge 3），均为既有失败，本轮未新增失败 |
| 类型检查 | `npx tsc --noEmit` | 通过 |
| 后端 lint | `uv run ruff check src tests` | 通过 |
| 迁移 | 本机 Postgres 实际建表 `assistant_vault_items`（11 列，与模型一致） | 通过 |

## 6. 真实页面验收（本机，同一提交）

环境：API `uvicorn harness.api.app:app`（生产容器、Postgres）+ Next dev（`:3000`），全新数据库 `harness_bot_1103`，全新验收账号。截图见 `docs/results/2026-11-03-kai-bot-vault-79/`。

1. `p13-header-name-only.png`——助理页头只有名称，右侧无头像/副标题/更多按钮。
2. `p13-random-appearance.png`——新建 Bot 弹窗默认「服务端随机分配」，创建后侧栏出现随机外观。
3. `p14-mention-receipt.png`——@ 选中 → 发送 → 居中回执「已发送消息给 · 头像 · 验收 Bot」。
4. `p15-vault-item.png`——设置内「助理资料库」：分类、搜索、新建、按 Bot 授权、编辑、删除；页面不含密钥明文。
5. `p13c-white-appearance.png`——白色外观在侧栏是灰底白形深眼（原为白底白形不可见）。

## 7. 未完成与阻塞（不得当作已完成）

1. **79 未发布**：本机没有 79（172.20.105.79）的 root 凭据——三把本机私钥均被拒，已知 174 口令在 79 也被拒；上一轮用的是 ControlMaster socket `/tmp/kai-observe-1102.sock`（已失效）与用户当次输入的口令。发布脚本已备好：`/Users/xiaokai/.codex/visualizations/2026/11/02/kai-superagent-plan/deploy-kai-bot-79.sh`（传输源码 → 在现网运行镜像上叠加构建 → 迁移 0065 → 切 api/worker → 健康检查；保留旧镜像标签与 compose 备份，可逐服务回滚），拿到凭据后 `SSHPASS=… bash deploy-kai-bot-79.sh` 即可。**79 上真实页面验收因此未做。**
2. **79 schema 未升级**：0065 只在本机库验证过。
3. **P15 的敏感填充/自动填充未实现**：按合同如实标注「不支持」，未声称。
4. **P14 回传路径的边界**：Bot 结果回到主会话依赖既有 notice 机制（有交办/职责时成立）；纯对话型 Bot 的回复留在它自己的会话，需从回执点名称进入。未额外造第二套回传通道。
5. **ADD-2（助理默认跟随平台联网）本轮未做**：需要核实已发布 Agent 的真实工具装配（79 上验证），本轮没有可用的发布窗口。
6. **首段公开文本 7.24/10.265 秒仍未达标**：属旧任务范围，本轮未改。
7. 本机验收用的是生产容器 + 本地 Postgres，不是 79；不能替代 79 实页结论。

## 8. 未触碰

- `auto/kai-assistant-managed-20261030` 工作树与其队列/模型（旧首段任务）。
- 174。
- 任何 IM 通道（暂缓）。
- 用户真实 Vault、真实 Bot 资料与消息；本轮全部用合成账号与合成数据。

---

# 补充：79 发布与 79 真实接口验收（01:16 恢复连接后）

## 8.1 79 当前状态核对（发布前）

- 容器：`api`/`worker` = `kai/superagent-api:20261102-842adcfd`（旧首段任务 01:00 前后发布），`web` = `kai/superagent-web:20261102-0db1e0b7`，schema `0064`。
- **并发**：切换前 `active_runs=0`、`active_objectives=0`（两次切换都核对过）。
- **其他 Agent 提交**：`842adcfd`（make the model speak before its first tool）与 `ef9710a6`（发布记录）在本分支基线之上，已 `merge`（`67ef9304`，无冲突），未覆盖对方提交。
- 私有 compose 备份：`/data/kai-79-20261013/compose.private.before-kai-bot-20261103.json`；回滚＝按该文件逐服务改回旧镜像。

## 8.2 发布记录

| 批 | 镜像 | 内容 | 结果 |
| --- | --- | --- | --- |
| 1 | api/worker `kai/superagent-api:20261103-8546636d`、web `kai/superagent-web:20261103-67ef9304` | P13/P14/P15 + 合并 `842adcfd` + ADD-2 | 迁移 `0064→0065` 成功；三服务 healthy |
| 2 | api/worker `kai/superagent-api:20261103-0025afa8` | 修掉「撤销授权没真正移除 credential_ref」 | 重新滚动成功；healthy |

- 证据目录：`/data/kai-bot-vault-20261103-67ef9304/`（`api-build*.log`、`web-build.log`、`rollout*.log`、`release-result.json`）。
- API 镜像在**现网运行镜像**上叠加（`FROM kai/superagent-api:20261102-842adcfd`），依赖集与现网一致；web 用干净 `node:22-alpine` 两段构建（9 层），不继承旧 bundle。

## 8.3 79 真实接口验收（合成账户，接口级）

用产品正常注册流程创建的合成账户 `zcode-accept-1103@local.test`（显示名「ZCode验收1103」，tenant `local`，role `member`）；未使用、也未伪造本人账户的登录态。

| 项 | 结果 |
| --- | --- |
| 取色表接口 `GET /specialists/appearance/palette` | 12 色 / 14 形 |
| 新建 Bot 随机外观 | 两个新 Bot 分别得到 `#7442d1/rounded-triangle`、`#0d9381/star`，互不相同、且都不是默认的 `#126fd4/blob` |
| 三项资料 `POST /specialists/{id}/profile` | 名称/标题/描述均按提交落库 |
| @ 投递回执 `POST /specialists/{id}/messages` | 生成 handover；**同一把 `Idempotency-Key` 重复提交只留一条**（handover 计数 1） |
| Vault 列表 | 只返回标题/分类/末四位/是否已保存，**响应与列表里都没有值** |
| Vault 卡片类 | 传 `cvv` → 422 拒绝；末四位可存 |
| Vault 授权 / 撤销 | 授权写入 `credential_ref:<标题>`；撤销后该引用从响应与回读里都消失（第一批发现的缺陷，已修并复验） |
| Vault 删除 | 标题不一致 → 409；一致 → 删除成功 |
| 存储层 | 凭据表里没有明文（`plaintext_secret_rows=0`）；Vault 行只属于合成账户 |

## 8.4 ADD-2：助理默认跟随平台联网（79 实测）

- **发布前实测**：79 上全部子助理（行业快报监控、编码协同、天气提醒、休息提醒）都跑 `archive-assistant-agent 0.1.1`，其 manifest 与**已发布工具目录**里都没有 `WebSearch/WebFetch`——平台联网开关是开的，但这些 Bot 实际搜不了；只有主助理的 `lead-agent` 能。
- **改动**：运行时新增 `assistant_scope` 标记（助理会话与助理例行执行），平台联网可用且本人没关闭时，助理自动获得平台内置搜索/抓取工具；复用既有 `WebConfigurationService` 与密钥，未新增第二套联网引擎；任务/项目面不继承，用户关闭立即生效。
- **79 证据**：
  - 部署镜像内 `claude_sdk.py` 已含 `assistant_scope` 分支 ✓；
  - 合成账户的真实助理运行 `run_eb90902e…`（`assistant_scope=true`、`conversation_surface=assistant`、状态 `succeeded`）的工具请求包含 `WebSearch`、`WebFetch` ✓；
  - 决定性用例（智能体自身**不声明**联网工具、且带已发布工具目录）由集成测试 `tests/integration/runtime/test_assistant_web_inheritance.py` 覆盖：注入后目录校验不失败、联网工具照样装配；任务面不继承；用户关闭时不装配。
  - 说明：该决定性用例**未在 79 上跑成真实运行**——合成账户的目录里只有 `lead-agent`（自带联网工具），`archive-assistant-agent` 不在平台目录里，走 `/v1/agui` 直接 404；其另一分支 `general-analyst` 的模型路由 `deepseek-v4-pro` 在该账户不可用（与本次改动无关）。因此「不带联网工具的智能体在 79 上真实联网」这一条**仍是接口/测试级证据，不是 79 实跑证据**。

## 8.5 79 真实页面验收：未完成（需要本人接管）

- 内嵌浏览器打开 `https://172.20.105.79:3543/assistant` 报 `ERR_CERT_AUTHORITY_INVALID`（自签证书）。按约束**不关闭证书校验、不越过安全警告**。
- 退到纯 HTTP `http://172.20.105.79:3501/assistant` 可加载，但 79 的 `AUTH_COOKIE_SECURE=true`，会话 Cookie 带 `Secure`，浏览器在 http 下不存储 → 无法登录。
- 也**未**自行签发/伪造既有用户 token、注入会话或改认证库来冒充既有登录态。
- 因此 **79 真实页面（页头三项资料、随机外观、@ 候选与回执、Vault 分类与脱敏）在 79 上未做实页验证**；本机同提交的实页证据见第 6 节。需要本人用自己的浏览器登录 79（或提供受信任的 HTTPS 入口）后补这一项。

## 8.6 第三批：补齐 P14/P15 合同里我自己漏掉的覆盖项（12a3d523）

对照合同 P14「覆盖同名、删除/停用、权限撤销、跨用户ID、重复提交、多目标及刷新」与 P15「撤销拒用、旧引用兼容」逐条核对，发现三处缺口并补上：

| 缺口 | 修法 | 79 实测 |
| --- | --- | --- |
| 停用/归档的 Bot 仍能被 @ 投递并留下回执 | 投递前拒绝归档与已暂停接单的 Bot（409，不写回执） | 暂停态 Bot 投递 → 409；`no_receipt_for_paused=true` ✓ |
| 删除资料条目后，Bot 手里还留着指向已删资料的 `credential_ref` | 删除即失效：同步收回所有引用该资料的 Bot 授权 | 授权 → 删除 → 回读 `ref_revoked_after_delete=true` ✓；Vault 行 0、凭据表无明文 ✓ |
| 一次 @ 多个助理时，一个失败会阻断其余 | 逐个投递互不阻断：已送达的照常发回执，失败的留在提及里可重试 | 前端代码 + 类型检查；接口层逐目标幂等键不变 ✓ |

## 8.7 79 发布批次汇总

| 批 | api/worker | web | schema | 状态 |
| --- | --- | --- | --- | --- |
| ① `8546636d` | `kai/superagent-api:20261103-8546636d` | `kai/superagent-web:20261103-67ef9304` | 0064→0065 | healthy |
| ② `0025afa8` | `kai/superagent-api:20261103-0025afa8` | 同上 | 0065 | healthy |
| ③ `12a3d523` | `kai/superagent-api:20261103-12a3d523` | `kai/superagent-web:20261103-12a3d523` | 0065 | healthy |

- 三批均在切换前核对 `active_runs=0`、`active_objectives=0` 与 schema；失败即按 `compose.private.before-kai-bot-20261103.json` 逐服务回滚。
- 79 现网前端产物已确认含新代码（静态 chunk 中可检出「助理资料库」「外观由服务端随机分配」「已发送消息给」）。

## 8.8 需要本人处理的一项（不冒充已完成）

79 真实页面验收需要本人用自己的浏览器处理自签证书并正常登录后完成；本轮未添加证书信任、未关闭校验、未伪造登录态、未改认证配置，也未用接口验收冒充实页。核对清单（页面 https://172.20.105.79:3543/assistant）：

1. 助理会话页头只有名称，无头像、无角色副标题、无更多按钮；点名称弹出「姓名／标题／描述」三项资料弹窗。
2. 新建 Bot 的外观由服务端随机分配（刷新页面不变），白色外观在侧栏为中性灰底白形深眼。
3. 输入 `@` 弹出可访问助理候选，回车选中后出现可删除的提及 chip；发送后出现居中「已发送消息给 · 头像 · 名称」。
4. 设置 →「助理资料库」：四类分类、搜索、新建/编辑、删除需确认标题、按 Bot 授权；列表与页面均不含密钥明文。
