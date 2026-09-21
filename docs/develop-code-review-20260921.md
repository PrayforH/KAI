# develop 分支代码审查（2026-09-21）

审查对象：`develop` @ `7c76d761`（相对 `origin/main` 共 329 个提交、816 个文件、+142,847/-5,079 行）。
`platform-skills/*`（上游 vendored 技能包）、`code-themes/*.json`、`uv.lock`、`package-lock.json` 属于引入的上游内容，不计入自研代码质量评估，但其中的版本引用与部署一致性在本轮被检查到（见 P2-19）。

方法：按区域分成 6 路并行审查（studio 子系统 / runtime 与 core / api 与 knowledge 与 storage / 前端 console / 测试 / 部署与迁移与配置），
每条结论要求给出 `路径:行号` 与代码证据；其中标「已复核」的由我逐条读过 develop 上的原始代码确认，标「待复核」的是审查代理读到代码后给出、我未逐条重读的结论。

---

## 一、总体判断

这条分支的问题不是"某些写法不好看"，而是三类同源的系统性习惯：

1. **把应该只有一个来源的东西抄了很多份**——同一个规则/常量/文本在 2~5 处各写一遍：默认 Agent 版本（4 处，互不相同）、终态状态集合（前端 5 处）、递归上限算法（导出器与运行时各一份）、工具门的两套覆盖规则、`uv.lock` 与样例 env 的版本、提示词的拼装（3 个 runtime 各一套）。真正的问题不是重复本身，而是**重复的副本已经开始分叉**，其中最重的两条已经造成线上行为与代码意图相反（P0-1、P1-3）。
2. **把字符串当契约**——用 `in json.dumps(payload)` 判断"打包脚本被调用过"，用 prompt 文本里出现某个文件名来认定产物名，用"错误文案相等"来清除错误状态，用 SDK 回显文本做引导投递回执，用异常消息子串做前端文案分类。这些判据的共同点：改动一边不会改动另一边，且失败方向往往是"静默通过"。
3. **重实现已有的东西**——手写 `asyncio.sleep` 轮询代替仓库已有的 `EventWakeup` 等待原语（至少 5 处：`api.py` eval 流、`triggers/a2a.py`、`application/approvals.py`、`claude_sdk.py`、`codex_runtime.py`），提示词与降级逻辑在多个 runtime 各写一遍。

用户最初指出的"把一大段 prompt 内联在方法里"（`worker_skill_creator.py:65`）属于第 1 类的一个具体形态：**它同时踩了三个坑**——长文本内联进代码、用户数据进 system prompt、以及把只有一处才能解释的业务约定散落在提示词散文里。

---

## 二、P0 / P1：建议先修

### P0-1 `src/harness/config.py:212-213` — 同一字段定义两遍，注释说的默认值被静默覆盖（已复核）

```python
# ... 90s left almost no margin before reporting a false readiness failure.
opensandbox_ready_timeout_seconds: int = Field(default=180, ge=10, le=900)
opensandbox_ready_timeout_seconds: int = Field(default=90, ge=10, le=900)
```

pydantic 保留后者，生效默认值是 90；而紧邻的三行注释正说明 180 才是按 174 上实测（54.7s / 80.8s）修正后的值。
唯一消费点 `sandbox/opensandbox.py:834` 拿到的是 90。删掉 213 行即可，并补一条断言默认值的单测防止再次发生。

### P0-2 `src/harness/knowledge/service.py:1150-1205` + `knowledge/weknora/gateway.py:169-184` — 文档级操作只校验 source 权限，不校验文档归属（已复核）

```python
await self._accessible_weknora_source(tenant_id, actor_id, reference)
item = await self._require_engine().get_document(document_id)   # 全局 ID，无归属校验
```

`get_source_document` / `delete_source_document` / `reparse_source_document` / `download_document` 的形态一致：
校验的是"你能不能访问这个 source"，然后按全局 `document_id` 直接操作远端。而 gateway 的 `delete_document(base_id, document_id)` /
`reparse_document(base_id, document_id)` / `download_document(document_id)` **把 `base_id` 收了却没用**，客户端路径是 `/knowledge/{document_id}`。

归属信息其实已经拿得到：`EngineDocumentStatus.knowledge_base_id`（`knowledge/ports.py:35`）在 `service.py` 里除第 781 行的检索映射外从未被使用。

影响取决于远端 document id 的可猜测性（可枚举的顺序 id 则是跨 base/跨租户的读取与删除；不可枚举时也是一个失效的授权检查）。
`download_source_document` 会直接把原始上传文件交出去，删除与重解析则是破坏性操作，因此**修复优先级最高**：
统一比对 `item.knowledge_base_id == source.config.weknora_base_id`，不匹配即 `NotFoundError`；同时让 gateway/client 真正按 base 走路径，做服务端二次约束。

### P1-1 `src/harness/runtime/sdk_tool_gate.py:676-686` — 缺 `implicit-deny` 前置条件，显式 DENY 被静默升级为放行（已复核）

```python
if (
    result.decision is PolicyDecision.DENY
    and raw_tool_name.startswith("mcp__harness-python-")   # 缺 rule_name == "implicit-deny"
    ...
```

同一个文件上一段的 `mcp__` 覆盖规则带了 `result.rule_name == "implicit-deny"`，`deepagents_tool_gate.py:305-312` 的对应规则也带了；
只有这一条没带，于是运维显式写的 DENY（例如禁用某个 Bundle Python 工具）会被改写为 ALLOW，且两个内核对同一次调用结论相反。
触发前提是策略里存在针对 `mcp__harness-python-*` 的显式 DENY（当前默认 profile 里没有），所以是"边界被打开"而非当前必然触发——但它是唯一一条策略绕过。

### P1-2 `src/harness/runtime/input_redaction.py:26-37` — Bash 读内部资产的判定靠命令动词白名单（已复核）

```python
if not isinstance(command, str) or not any(
    token in command.casefold() for token in ("cat ", "sed ", "head ", "tail ", "grep ", "rg ", "awk ")
):
    return bool(payload.get(INTERNAL_AGENT_ASSET_MARKER))
```

`python3 -c "print(open('.claude/skills/x/SKILL.md').read())"`、`sort`/`strings`/`od`/`less`/`cat<file` 都不命中，返回 False 即不脱敏。
而 `runtime/deepagents_runtime.py:76-84` 把"平台会脱敏任何读 `.claude/skills/` 的调用"当作不变量写在注释里，并据此声称
"a Skill body can never reach the durable event stream"——这个保证当前不成立。Read 分支已经是按路径判定，Bash 侧不应按动词白名单。

### P1-3 `web/harness-console/src/components/agent-studio/automation-manager.tsx:176-215` — 编辑保存会静默改掉定时任务的调度（已复核）

```ts
const [minute, hour, , , weekdayField] = parts;   // dom / month 丢弃
if (hour.startsWith("*/")) { frequency = "weekdays"; }   // 不看 dow
// ...
if (form === "weekdays") return "0 */2 * * 1-5";   // buildCron 写死
```

`formFromTask` 把任何 `*/N` 小时都判为"工作日"、把任何带 `*` 的 dow 判为"每天"，`formToInput` 只要不是 custom 就按模板重建 cron。
于是：`0 */4 * * *`（每 4 小时）打开编辑再保存 → 变成 `0 */2 * * 1-5`；`0 9 15 * *`（每月 15 日）→ 变成每天 09:00。
`scheduleSummary`（同文件 78-81 行）同样把 `*/N` 一律显示成"工作日每 N 小时"，所以列表文案和实际执行也不一致。
用户无法从界面察觉，属于数据损坏级。改法：只有 cron 与模板串**逐字相等**时才反推频率，否则一律 `frequency="custom"` 并原样回填。

### P1-4 `src/harness/storage/sandbox_lease_repository.py:99-115` — 自称 CAS 的 `replace` 实际是读后写（已复核）

```python
row = await session.get(SandboxLeaseRow, (lease.tenant_id, lease.lease_id))
if row.epoch != expected_epoch: return False
row.epoch = lease.epoch
await session.commit()
```

没有 `with_for_update`，UPDATE 也不带 epoch 条件，两个并发持有者会双双通过检查并互相覆盖（lost update）；
reaper 与 owner 并发时，stale owner 仍能改写已回收的租约。而 `sandbox/lease.py:130` 的互斥只由**进程内** `self._lock` 保证，
多副本 worker 下不成立。改法：条件进 SQL（`update(...).where(..., epoch == expected_epoch)` 用 rowcount 判定）或 `SELECT ... FOR UPDATE`。

### P1-5 `tests/integration/studio/test_deepagents_export_runtime.py:18-19` — 唯一真正执行导出工程的测试被静默跳过（待复核）

```python
RUNTIME_PYTHON = os.environ.get("DEEPAGENTS_TEST_PYTHON")
pytestmark = pytest.mark.skipif(not RUNTIME_PYTHON, reason="isolated DeepAgents runtime required")
```

CI（`.github/workflows/verify.yml`）从不设置该变量，所以"导出工程能否真的跑起来"在 CI 中零覆盖，而这是本分支的核心交付。
同一主题下的 `tests/unit/studio/test_deepagents_export.py` 只比对生成文本，产物不可运行也不会红。

### P1-6 `src/harness/agui/service.py:379-411` — 标题生成失败后每轮列表刷新都重排一次模型调用（已复核）

`resolve_title` 的早退条件是 `title_source in {"model","user"}`；`_generate_model_title`（同文件 512-535）失败时
`except Exception: return`，不写任何"已尝试"标记，`title_updated_at` 也不更新。于是失败的线程在每次 `resolve_title`
（`agui/routes.py:659` 对每个 binding 调一次）都会重新排一次任务，`_title_task_keys` 只在任务在飞时去重。
模型路由不可用时 = 每次列表轮询为每个线程重试一次。改法：把尝试状态落到 binding 上（失败也写时间戳/退避），并给列表级并发上限。

### P1-7 `web/harness-console/src/components/agent-studio/skills-catalog-page.tsx:35,164-176` — 只改 localStorage 的开关被呈现为平台状态（待复核）

`toggleSkill` 只写 `harness-skill-catalog-disabled:v1`（全仓仅此一处引用），开关是 `role="switch"`，详情文案写"已禁用 · 需要在新版本发布时固化为不可变快照"。
操作者会认为平台技能已停用。要么接后端启停接口（含乐观回滚），要么把语义改成"本机隐藏"并去掉 switch 角色。

### P1-8 `web/harness-console/src/components/task-header-actions.tsx:308-346` — 置顶/归档失败用户完全看不到（待复核）

错误元素只在 `{open && ...}` 内渲染，而两个动作都是先 `close()` 再 `run()`，`setError()` 写进已经卸载的菜单，失败静默。

### P1-9 `web/harness-console/src/components/agent-studio/agent-preview.tsx:38` — 内部错误码直接给用户，翻译函数是死代码（已复核）

```tsx
: `本轮未完成。${result.run.error_code || "请查看执行详情后重试。"}`}
```

`tryRunFailureMessage()` 定义在 `agent-builder-overlays.tsx:73`，全仓无调用点（grep 确认只有定义处）。

### P1-10 `src/harness/studio/catalog_service.py:386` — `next()` 无默认值，vendored 树缺失会让整个目录读路径不可用（已复核代码）

```python
canonical_creator = next(s for s in default_capability_catalog().skills if s.package_id == "skill-creator")
```

`skill-creator` 来自 vendored `platform-skills/`。该目录缺失或 `_parse_frontmatter` 抛错时，这条路径上的每一次目录读取
（`/capabilities`、编译、Builder）都会失败，协程里 `StopIteration` 还会被包成 `RuntimeError`。
`vendor_skills.load_vendored_skills` 的 docstring 明确承诺"返回不了就什么都不返回"，即此处本应优雅降级。
改法：`next(( ... ), None)`，`None` 时跳过 label/退休迁移。

---

## 三、用户指出的那类写法：长 prompt 内联（`worker_skill_creator.py:65`）

这一点是**一类问题的一个实例**，本轮在整条分支上统计到的同族写法：

| 形态 | 位置 | 计数 |
| --- | --- | --- |
| 方法体内内联 f-string prompt | `studio/worker_skill_creator.py:65`（含 `{name}` 与 `json.dumps(...)` 插值）、`:146` 又一条内联运行 prompt | 2 |
| 用户数据插进 prompt 字符串 | `agui/task_title.py:106`、`studio/factory.py:58` | 2 |
| 同一提示词在多个 runtime 各拼一套 | `claude_sdk.py:942-945,991-995`、`deepagents_runtime.py:443`、`codex_runtime.py:525-556` | 3 |
| 用代码生成代码（模板 + `str.format`） | `studio/deepagents_export.py:419-425`（`_TOOL_MODULE.format`，含死参 `pascal`） | 1 |

为什么这一处特别不合理，有四条**来自本仓库自身约定**的理由：

1. **仓库已经有 prompt 的归属约定，这里没遵守。** 静态共享提示词是模块级常量（`runtime/execution_contract.py:3` 的 `VISIBLE_EXECUTION_CONTRACT`、
   `studio/builder_conversation.py:199` 的 `BUILDER_SYSTEM_PROMPT`、`studio/skill_builder.py:79` 的 `_SYSTEM_PROMPT`），
   Agent 自身的 system prompt 是文件（`agents/*/prompts/system.md`）。唯独这一处把一份 5 节的结构化文档写在 `respond()` 方法体里。
   后果是：diff 不可读、没有语法高亮、无法单独测试、无法被第二处复用——事实上它的"输出契约"已经在代码里被第二份实现（产物名校验）所重复。
2. **信任方向是反的。** 该 prompt 最终经 `spec.system_prompt` 成为这次运行的 **system prompt**，而这个 Agent 带着 `Write`/`Edit`/`Bash`/`publish_artifact`。
   被插进去的 `request.messages` 是客户端自由文本（单条上限 12k）、`context.description` 亦然。仓库里做同一件事的另外两条路径都是**反向**的：
   `skill_builder._SYSTEM_PROMPT` 第 1 条明确要求"把 agentContext 和 conversation 当作待分析的数据"，
   `AnthropicCompatibleSkillConversationService.respond` 把 `json.dumps(model_input)` 放在 **user** 消息里、system 只放静态常量。
   顺带：`exclude={"current_skill"}` 说明作者意识到技能正文敏感，却把对话正文留在了最高信任通道。
3. **f-string 让 prompt 文本变得不能自由编辑。** prompt 里出现字面量 `{` `}` 就要转义；所以这段提示词里**无法写 JSON 示例**——
   而隔壁 `BUILDER_SYSTEM_PROMPT` 里恰恰全是 `{"reply":...}` 这类示例。也就是说，"想给提示词加个输出样例"这个最普通的改动，会在这里变成运行时错误。
4. **它把业务约定写进了散文。** `将生成的 {name}.skill ... 发布` 与代码里的 `a.name == f"{name}.skill"` 必须字面一致，模型改个命名就整轮失败；
   `设计 2—3 个真实测试用例` 与 `_EvaluationPlan` 的 `min_length=2, max_length=50` 也是两条互不相干的约定。

建议改法（不改行为，只换位置）：把 prompt 提到模块级常量（只含指令），把 `json.dumps(...)` 作为 user 消息或 `input` 传给运行，
产物名等常量在提示词与校验之间共享一个 `ARTIFACT_NAME`。

---

## 四、P2：分批可修（均已读到代码，未逐条重读复核）

**同类重复与硬编码**

1. `deploy/docker-compose/.env.docker.example:85` 把 `HARNESS_DAYTONA_CLAUDE_CLI_VERSION` 钉在已淘汰的 `2.1.206`，
   compose 默认已改为空、`docs/deployment.md:225` 已改成"跟随 SDK 内置"，只有样例没改 → 采用默认 sandbox provider 的部署会在 provisioning fail-closed。
2. 默认 Agent 版本 4 处互不相同：`agents/lead-agent/agent.yaml:5`(1.0.3) / `.env.docker.example:148`(1.0.0) / `compose.yaml:370`(1.0.0) / `deploy/web-79/compose.yaml:265`(1.0.1)，
   而 `platform_repositories.py:74-78` 是精确版本查找、无回退 → 版本对不上就是 `NotFoundError`。
3. `deepagents_export.py:943,1059` 用行内算式重算 `has_bash` 与 `recursion_limit`，而 `deepagents_plan.py` 已有 `plan.has_bash` / `plan.recursion_limit`（运行时读的是 plan）→ 导出代码视图与真实执行可能不一致。
4. `deepagents_export.py:521` 与 `:1090` 各推导一次"子智能体模型环境变量名"，再靠 `str.replace` 对齐 README/.env；分叉即静默用默认模型。
5. `platform_skills.py:109,142,187` 用三张手写 dict 索引同一组 vendored 技能名，缺项是 `KeyError`（500）且无 import 期一致性校验。
6. `studio/api.py:1905-1919` 等 4 处各写一遍 `install_skills` 授权判断，其中 `converse_agent_builder` 是**干完活之后**才判（模型调用与沙箱运行已发生，只为了让请求以 403 结束）。
7. `studio/model_configuration.py:496-501` 与 `1136-1138` 各写一遍 Anthropic `/v1` 路径规则，条件不一致、超时 90s vs 30s。
8. 前端终态状态集合 5 处各写一遍且成员不一致：`agent-builder-overlays.tsx:176,247,549`、`agent-preview.tsx:16`、`task-sidebar.tsx:123`。
9. `composition.py:804-806,263-266` 硬编码容器内绝对路径 `/app/agents/lead-agent/agent.yaml` 与 route id 字面量。

**用字符串当契约**

10. `worker_skill_creator.py:185-196` 用 `"scripts.package_skill" in json.dumps(e.payload)` 认定"官方打包被调用"（payload 已有结构化 `arguments`），产物按精确文件名匹配。
11. `runtime/message_mapper.py:119-127` 用 `repr(...).lower()` 搜 `agentid`/`output_file` 判断是否隐藏内部元数据；`:45-70` 的错误分类基于英文子串，未命中就原样透出（方向是泄漏）。
12. `agui/activity.py:333-336` 靠另一个模块异常消息里的子串做用户可见错误分类。
13. `agent-builder-overlays.tsx:238-246` 用错误文案字符串相等来清除错误状态。
14. `claude_sdk.py:490-504` 用"CLI 回显文本 == 队列引导文本"做投递回执，任何归一化都让该引导永久 pending。
15. `tests/unit/runtime/test_registry_runtime.py:1414`、`test_claude_input_files.py:173`、`tests/integration/api/test_builder_conversation.py:250` 断言 prompt 文本子串。

**手写轮询 / 已有原语未用**

16. `studio/api.py:812-836`（eval 流 0.5s）、`triggers/a2a.py:518-529`（50ms，而同文件 684 行就在用 `wait_for_run_event`）、`application/approvals.py:102-121`（250ms）、`claude_sdk.py:447-452` 与 `codex_runtime.py:366-381`（各写一份 250ms 引导投递循环）。
17. `deepagents_events.py:154-165,226-246` turn/usage 记账绑定内部图节点名字面量 `"model"`/`"tools"`；`_seen_nodes` 收集了却从不使用；
    usage 解析不到时 `num_turns` 被 `max(1, ...)` 兜成 1 → 用量/成本可能被记成 0（该 payload 进配额账本与 Langfuse）。

**能力与数据的边界**

18. `deepagents_plan.py:131-140` 对无法映射的平台内置工具直接 `continue`，`unmapped_builtin_tools` 的收集结果无人读，而编译器会把含 `WebSearch/WebFetch` 的草稿判为"平台内置公开网页检索"→ 能力被静默丢弃且运行期无任何说明。
19. `deepagents_export.py:558-575` 生成的 `agent.py` 在 import 时无条件 `rmtree` 工作区里的 `workspace/skills/`（README 又把 `WORKSPACE_ROOT` 说成文件工具的工作根）→ 每次进程重启删掉用户那里的状态。
20. `studio/skill_import.py:211-221` 的 `validate_authored_skill` 自称与上传路径同规，实际只做凭据文件检查，丢了 `import_skill` 的路径安全/体积限制与 `scripts/*` 风险标记 → 同样的 `scripts/install.sh` 经导入会告警，经 Skill Creator 不会。
21. `knowledge/service.py:410-448` `list_syncs`/`list_snapshots` 先 `limit=max(limit, 10_000)` 全量拉取再在 Python 里按"自己拥有的 source"过滤（API 层 `limit ≤ 200`）。
22. `knowledge/service.py:270-276,936-964` `list_bases` 对每个 base 做远端分页（最多 50×100 条），且 `_refresh_document_count` 把刚取到的 documents 又拉一遍。
23. `knowledge/service.py:715-719` 检索时把绑定快照下全部 chunk 读进内存再排前 8；`agui/routes.py:831` 线程历史用 `limit=200` 截断却把 `total` 当真实总数（超过 200 个 Run 的线程静默丢最早历史）。
24. `knowledge/directory.py:50-101` 成员目录不按租户过滤（`tenant_memberships` 存在且 `auth.list_members` 是按租户查的）→ 任何 `tasks:read` 用户可枚举全平台用户邮箱/姓名。
25. `knowledge/service.py:1640-1655` `add_members` 是 check-then-act + 逐个 commit，并发下部分写入已提交且不写审计；已存在成员被静默跳过（把 viewer 提为 editor 会返回 201 但没改）。
26. `config.py:67-75` `AliasChoices("automation_agent_name","HARNESS_AGENT_NAME")` 在 `env_prefix` 下不会读带前缀的键（pydantic-settings 行为），文档却教人设 `HARNESS_AUTOMATION_AGENT_VERSION`；且与前端默认 Agent 共用变量、compose 只注入 Web。
27. `deploy/docker/api.Dockerfile:82-85` 把 `docx`/`pptxgenjs` 装在 `/usr/local/lib/node_modules` 并依赖 `NODE_PATH`，而沙箱执行重建环境只留 HOME/PATH/TMPDIR；两个增量 Dockerfile 已修，主镜像没修。
28. `config.py:199` `cubesandbox_validate_template` 默认 `False`，与自身注释和两份 env 样例（`true`）矛盾。
29. 新增配置项在 compose/样例里无出口（`sandbox_extra_providers`、`egress_enforcement`、`cubesandbox_volume_mounts`、`web_search_provider` 等 grep 命中 0），而 `web_search_provider` 默认已从 tavily 翻成 minimax，样例只透传 `HARNESS_WEB_SEARCH_API_KEY`。
30. `storage/models.py:1207` + `projects/pg_repositories.py:43` 项目唯一约束建在 `(tenant,user,name)` 上、不看 `archived_at` → 归档后同名项目永久无法创建，只得到一句无解释 409。
31. `migrations/versions/0035_projects.py:22-38` upgrade 有存在性守卫、downgrade 没有；索引在迁移事务内非并发建（大表会阻塞写入）；0035 无对应测试。
32. `deploy/docker/web-runtime-reuse.Dockerfile:2` `RUNTIME_BASE` 默认指向一个具体历史发布 tag，漏传 `--build-arg` 时静默在旧基座上叠新产物。
33. 文档与代码不一致：`docs/deployment.md:107-110`、`docs/local-development.md:69,74` 仍在教配置已退役的 `tavily-readonly`（`catalog.py:22` 已列入 retired），
    且 `tests/e2e/test_local_stack.py:107` 把这个过期文档断言钉死；`docs/domain-agents.md:164` 的版本号与 manifest 不符。
34. 前端同一 Wiki 内容两套渲染实现：`knowledge-wiki-panel.tsx:364` 用正则+逐行切片，`wiki-page-drawer.tsx:32` 用 `react-markdown` → 同一页面在面板与抽屉里显示不同。
35. `lib/task-history.ts:274-303` `loadEarlier` 在 await 前捕获旧 `entry`，回来后以旧快照为基底合并 → 期间到达的新消息被整段丢弃并 import 回运行时。
36. `components/agent-studio/agent-studio-workbench.tsx` 4337 行，草稿 CRUD/发布/部署/评测轮询/策略治理/MCP 目录/Skill 创建与全部渲染在一个组件里；评测跟随循环（约 1918-1942）与 `agent-builder-overlays` 的重连逻辑是两套近似实现。
37. `lib/use-drawer-resize.tsx:42-61` 指针监听只在 `onUp` 里解绑，拖拽中被关闭时监听常驻、`body.userSelect` 停在 `none`。

---

## 五、测试：测得多，但测不住

新增约 12,350 行测试，问题不在数量而在**断言对象**——多数断言落在最容易写的地方（源码字符串、prompt 文案、私有属性、mock 调用次数），
而不是需要搭环境才能观察的行为（渲染出的 DOM、Postgres 的行状态、真实内核的执行结果、外部服务响应）。

- **前端 21 个新 spec 只读源码再 `toContain`，从不 render**（`workbench-layout.spec.ts:5-68`、`agent-studio-layout.spec.ts` 77 处、`knowledge-graph-3d.spec.ts:66` 断言 `SphereGeometry(radius, 24, 24)`）。
  117 个 spec 里只有 25 个真正挂载组件。UI 返回 null、按钮不接 onClick 都全绿。
- **新增的 Postgres 租约仓储在生产装配里（`composition.py:716`），但全部租约测试只注入内存替身**，且内存替身与真实实现分歧（`add` 静默覆盖 vs 抛 `ConflictError`）。
  "每 session 仅一个 live 租约""stale owner 不能改写"两个安全性质只在它们必然成立的配置下被断言。
- **需要真实依赖的验证被删除或静默跳过**：`tests/integration/runtime/test_tavily_mcp_live.py` 整文件删除（含凭据注入与 `ContextTrust.UNTRUSTED` 断言）且无替代；
  导出工程执行测试靠未在 CI 设置的 env 门禁跳过。
- 断言 prompt 文本 / 私有属性 / mock 调用序列；`tests/unit/test_migration_0033.py` 把迁移本体 mock 掉只验证"调用过 mock"；两个 spec 引用**测试里手抄的 Lua 脚本**做 fake；
  集成测试反向 import 单元测试私有 fixture（`test_deepagents_graph.py:30-31`）。

根因是配套的结构性缺失：替身按"让代码能跑"而非"复刻真实语义"造；没有覆盖率门禁，`vitest` 无阈值、`pytest` 不报 skip，
所以"跳过"和"只 grep 源码"都不会被拦下。

---

## 六、建议的修复顺序

1. `config.py:212-213`（一行）、`catalog_service.py:386`（一行）、`deploy/docker-compose/.env.docker.example:85`（一行）——三个一行的修复，先清掉。
2. `knowledge/service.py` 文档归属校验 + gateway 真正使用 `base_id`——唯一的越权/破坏性操作面。
3. `sdk_tool_gate.py:676-686` 补 `implicit-deny`，并把两条覆盖规则抽成共享谓词由两个 gate 调用。
4. `automation-manager.tsx` 的 cron 往返——静默改坏用户调度，用户不可察觉。
5. `sandbox_lease_repository.replace` 的条件写进 SQL + 补 Postgres 集成测试。
6. `input_redaction.py` Bash 判定改为按受保护路径，而不是命令动词白名单。
7. 再动"重复与字符串契约"这一类：先落共享常量/共享谓词（默认版本、终态集合、递归上限、授权守卫、提示词常量），再逐处替换。
8. 测试侧：删掉源码 grep 类 spec，给租约与导出工程补真实依赖的测试，并让 CI 不再静默跳过。

---

## 附：本轮未做的事

- 未修改任何代码；本文档只记录结论。
- 未运行测试/构建（工作区在另一分支且依赖不全），所有结论来自静态阅读。
- `platform-skills/*` 内的上游文件只做了引用与版本一致性检查，未做代码质量评估。

---

# 修复与 174 验证（2026-09-21）

修复落地在 `fix/develop-review-20260921`（基于 `develop@7c76d761`），提交 `aa0e8f35`、`118298f6`、`fe9bb761`，
并按 `REVIEW-FIXES-174.md` 部署到 174 验证。

## 已修

审查里的 **2 条 P0 与 8 条 P1 全部修完**，另加一组同源的 P2（重复与字符串契约）：

| 问题 | 改法 |
| --- | --- |
| `config.py` 重复字段把 180s 静默改成 90s | 删除重复定义，并让单测断言这个来自实测的默认值 |
| 知识库文档级操作只校验 source、不校验归属 | 新增 `_require_owned_document`，与引擎返回的 `knowledge_base_id` 比对；10 处 base 读取统一走 `_engine_base_id` |
| SDK 工具门缺 `implicit-deny` 前置条件 | 两份门共用 `runtime/tool_allow_overrides.py`，回归测试证明旧行为会失败 |
| 脱敏按 shell 动词白名单判定 | 改为按受保护路径判定（Read 与 Bash 同一规则） |
| 租约 `replace` 名为 CAS 实为读后写 | epoch 条件写进 UPDATE，缺失区分 `NotFoundError`；真 Postgres 集成测试断言"判定在写内、且不先读" |
| 前端 cron 往返静默改掉调度 | 抽出 `lib/automation-schedule.ts`，只有与模板逐字相等才反推频率，其余保持 `custom`；顺带修掉 `每周周五` 文案 |
| Skill Creator 提示词内联 + 用户数据进 system prompt | 指令提为模块常量，需求改走数据消息，打包证据从序列化子串改为工具参数，等待改用共享唤醒 |
| 目录读取在缺 vendored 树时崩 | `next(..., None)` 后跳过标签刷新 |
| 标题生成失败后每轮列表都重排模型调用 | 失败写入冷却窗口，单元测试证明去掉冷却即失败 |
| 终态集合/错误文案/状态联合在多处各写一遍 | 收敛到 `lib/run-status.ts`，`StudioTryRun` 复用同一联合类型 |

## 174 验证

- 后端镜像与本地源码逐字节一致（318 文件，聚合 SHA-256 `48760584…f235de`）；API 与 3 个 worker 均为 `reviewfix-20260921`、healthy。
- 运行态复核：`opensandbox_ready_timeout_seconds=180`、`cubesandbox_validate_template=True`。
- 文档归属：源 `aipolicy` 读 `overseas` 的文档 —— 引擎侧仍 200，平台侧详情与切片均被拒；同一文档经自己的源可读。
- 脱敏与 Creator 提示词：`python -c open(...)`/`sort`/`cat` 命中脱敏，用户产物不误伤；提示词五个必需小节齐全且产物名与校验一致。
- 真实运行 `run_538d410de5094c5e83db30a555c23368`：succeeded、runtime=deepagents、1 轮、5013/348 tokens。
- 3301 与 3501 均运行 `agent-studio-web:reviewfix-20260921`，health=healthy、HTTP 200，产物中可见新文案且旧兜底文案为 0 命中。
- 分支已 rebase 到 `origin/develop`（上游另有 2 个提交修了同一处 config 重复字段并加了 desktop local 设置项；
  迁移头那处保留"自派生"而不是写死 `0035`），随后快进合并并推送：`origin/develop = 0523fc10`。
- 镜像在**本地** buildx 构建 linux/amd64 后推 Harbor，174 只拉取，当前部署即 `develop@0523fc10`：
  `agent-studio-api:develop-20260921-0523fc10`（digest `sha256:a7edb92e…9d58`，同时承担 api 与 worker 两种 entrypoint）、
  `agent-studio-web:develop-20260921-0523fc10`（digest `sha256:9a542f5f…d017`），3501 同镜像。
  镜像内 harness 与本地源码聚合哈希一致（`74ca13ae…f9af`，318 文件），配置、归属、脱敏/提示词与真实运行
  （`run_c81ccb0d…`，succeeded）四项验证在 develop 镜像上重跑均通过。
- 未验证项与回滚步骤见 `REVIEW-FIXES-174.md`。
