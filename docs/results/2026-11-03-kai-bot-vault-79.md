# KAI Bot 交互与 Vault 收敛（至1103 0830）· 结果

- 工作树：`/Users/xiaokai/.codex/worktrees/kai-bot-20261103/agent studio`
- 分支：`auto/kai-bot-vault-20261103`（基线 `60ef9143`，代码 `0db1e0b7`）
- 提交：`08ceba23`（P13）、`e5916955`（P14）、`40e8f0df`（P15）、`<本文件提交>`（验收与缺陷修复）
- schema：新增迁移 `0065_assistant_vault_items`
- 79 发布：**未执行**（本机无 79 root 凭据，见「未完成与阻塞」）
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

1. **79 未发布**：本机没有 79（172.20.105.79）的 root 凭据——三把本机私钥均被拒，已知 174 口令在 79 也被拒；上一轮用的是 ControlMaster socket `/tmp/kai-observe-1102.sock`（已失效）与用户当次输入的口令。发布流程（叠加镜像 → 迁移 0065 → 切 api/worker/web → 健康 → 真实页面）已就绪，拿到凭据即可执行。**79 上真实页面验收因此未做。**
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
