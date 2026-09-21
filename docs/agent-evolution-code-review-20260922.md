# agent-evolution 分支代码评审（2026-09-22）

范围：`auto/agent-evolution` 相对 develop 的自有改动（`git diff origin/develop...HEAD`，
136 文件 / +7998 −674），合并 develop 之后的状态（merge commit `a5f4d36a`）。
关注点按用户要求：**代码是否优雅、接口是否清晰**。

方法：先按目录分片通读新增文件，再由本人逐条复核关键项（下文凡标「已复核」的都在工作区
读过源码；标「未复核」的只有静态检索证据）。基线：`tsc` 无错、vitest 811 passed /
1 skipped、`pytest tests/unit` 1473 passed。

## P0

| # | 位置 | 证据 | 影响 |
| --- | --- | --- | --- |
| P0-1 已复核 | `src/harness/evolution/service.py:194-216` | `updated = job.model_copy(update={**changes, "revision": ...})` | 唯一的写入路径绕过 pydantic 校验。`models.py:182` 的 job status、`models.py:108-119` 的 candidate status（`Literal`）与 `models.py:193-198` 的 `unique_candidates` 校验器**只在读回时**才生效（`pg_repository.py:47` `EvolutionJob.model_validate(row.payload)`）。写错一个字符串会先落进 JSONB，稍后以 500 暴露。 |
| P0-2 已复核 | `service.py:181-185` vs `:404,639,664,718,736` | `_active()` 同时检查 revision + `status != "active" or now >= expires_at`；上述 5 处只写了 `if job.revision != revision` | 手写副本丢掉「已停止/已过期」这一半：`set_experience`、`observe` 等入口可以继续修改已取消或已过期的 job。同一规则 6 处各写一遍，本身就是这次要消掉的重复。 |
| P0-3 已复核 | `web/harness-console/src/lib/auth-route.ts:172` vs `src/lib/auth-session.ts:19-21` | `readCookie(request, "harness_refresh_token")` 硬编码，而同分支新增的 `cookiePrefix = process.env.AUTH_COOKIE_PREFIX ?? "harness"` 决定真实 cookie 名 | 本次部署的 173 验证栈正是 `AUTH_COOKIE_PREFIX=harness_evolution`（`deploy/evolution-validation/README.md:11`），因此 `logoutSession` 读不到 cookie，**登出不会吊销上游 refresh token**。属于隔离部署下可复现的真实缺陷。 |
| P0-4 未复核 | `service.py:66` + `pg_repository.py:47,55` | `except (ConflictError, NotFoundError): continue`，而仓储抛的是 `ValueError("Corrupt evolution persistence envelope")` 与 pydantic `ValidationError` | 一条损坏/历史 payload 会中断整个 `reconcile_pending` 循环，其余 job 不再回收；同一异常到 HTTP 层是 500 而非映射后的 4xx。 |

## P1

| # | 位置 | 证据 |
| --- | --- | --- |
| P1-1 已复核 | `service.py:366,568,581,597,621,647` | 同一个「快照/包哈希是否相等」判据手写 6 次：`(compiled.report.snapshot.content_hash, compiled.report.package_hash) != (...)`。其中一个站点（`:581` 起，release 守卫）漏判就是发布门禁漏洞。应收敛成一个 `_matches(...)`。 |
| P1-2 已复核 | `src/lib/agent-templates.ts:137` | `domain: template.id`，而 `StudioDraft.domain` 的真实取值是 `"general-assistant"`/`"productivity"`（`src/lib/agent-studio.ts:466,579`）。模板 id（如 `pr-reviewer`）被当成 domain 持久化。 |
| P1-3 已复核 | `agent-builder-overlays.tsx:434` 与 `:598` | 同一段识别逻辑逐字重复两次：`const explicitRun = /^(?:\/run(?:\s|$)|试跑(?:智能体)?\s*[:：])/i.test(value.trim());`，紧随的 `authoring` 也一样。该规则应和 `isAgentConfigurationRequest` 同处（`src/lib/agent-conversation-intent.ts`）。 |
| P1-4 未复核 | `evolution/service.py` 的 status 字符串 | job status 在 `models.py:182` 定义，却在 `service.py:184,392,406,575,592,669,673`、`repositories.py:26`、`pg_repository.py:22` 以裸字面量比较；candidate status 同理遍布 `service.py` 十余处；`api.py:126` 用 `Literal["reviewed","deprecated"]`、`service.py:723` 又用 `not in {"reviewed","deprecated"}` 再判一次。同项目已有正确范式（`evals/models.py:16` 的 `EvalRunStatus(StrEnum)`）。 |
| P1-5 未复核 | `service.py:684` 嵌套 `evals.list_runs` | 取消路径 `for candidate → for trial → list_runs(tenant, owner)` 是 O(candidates×trials) 次全量列举，且对已经 terminal 的 run 也会再写一条 `studio.eval_run.cancel` 审计（`evals/service.py:339-364`）。 |
| P1-6 未复核 | `src/lib/conversation-scope.tsx:9-22` | 一个 context 里同时放 6 个必需回调（`onOpenFiles`/`onNew`/…，`agent-playground-thread.tsx:292-305` 甚至要手写 store 内部快照 `{status:"idle",text:"",visible:false}` 去满足它），把状态与命令耦合在一起，调用方只能造内部形状。 |
| P1-7 未复核 | `src/lib/run-status.ts:22-42` 的重写点 | `agent-playground-thread.tsx:31-37` 自建 `new Set([...])` 终态表；`agent-overview.tsx:56-67`、`agent-operations-workspace.tsx:20`、`evolution-workspace.tsx:11` 各自再写一份中文标签；`agent-workspace.ts:14-17` 与 `evolution-workspace.tsx:89-93` 对 candidate 状态给出**两套不同字面量集合**。 |

## P2

- `models.py:189` `generation_attempts` 全仓无读写，注释却声称用于滚动升级保留。
- `service.py:65` `count += updated.revision != job.revision` 用布尔当计数，含义不是「回收了几个 job」。
- `service.py:673` 过期写成 `status="budget_exhausted"`，`test_workflow.py:279` 把这个标签固化下来。
- `service.py:227-259` 用 `dict[str, Any]` 做 prompt/skill 局部改写（`container[key] = after`），`AgentDraftSpec` 的类型保护在这里就丢了。
- `evals/diagnostics.py:13,21` 的 `ConfigDict` 漏了 `extra="forbid"`，与同目录 `evals/suite.py:16-17`、`studio/models.py:22-23` 的约定不一致。
- `evolution-client.ts:24-29` 用 `body === undefined` 决定 GET/POST，显式传 `undefined` 会静默变 GET；`status`/`decision` 保持 `string` 而调用方只传固定集合。
- `auth-route.ts`-类问题同源：`agent-workspace.ts:20-22` 用魔法前缀 `!s.agentVersion.startsWith("evo-")` 表达规则且无注释。
- `agent-template-gallery.tsx:117-131` 在 `<button>` 里嵌 `<p>/<strong>/<small>`（无效内容模型）；`agenta-configuration.tsx:35` 硬编码 `aria-expanded="true"`；`agenta-configuration.tsx:22-24` 声明 `onPublish/onCode/onBuildChat` 却从不使用，调用方 `agent-studio-workbench.tsx:4008` 仍在传。
- `migrations/versions/0036_evolution_control_plane.py:17` 直接 `EvolutionJobRow.__table__.create(...)`：可接受（新表、无回填、无锁），但迁移导入可变 ORM 模型后不再可重放；`downgrade` 直接删表且无导出。
- 测试：`test_workflow.py:270-276` 用 `model_copy(update={"expires_at": ...})` + 直接写仓储来造过期，绕过了被测服务路径，真实过期逻辑坏掉也仍然通过；`:298-302` 只有「没抛异常」没有断言；`PostgresEvolutionRepository` 无任何测试（`repositories.py:26` 与 `pg_repository.py:22` 的状态判据因此可能静默漂移）。

## 结论与建议顺序

接口层面 backbone 是清晰的：`evolution/repositories.py:12-17` 的 Protocol 把持久化抽象得干净，
`service.py` 承担用例编排、`api.py` 只做鉴权与转换，方向正确。真正要修的是三类**同一规则多处手写**：

1. 终态/状态字面量（P1-4、P1-7）→ 收敛到已有 `run-status.ts` / 新增 evolution status 常量；
2. job 活性判据（P0-2）与哈希相等判据（P1-1）→ 各留一个函数；
3. 写入校验（P0-1）→ `_save` 改为 `model_validate` 后再落库，让 `Literal` 真正生效。

P0-3（cookie 前缀）与 P0-2 值得在本轮一并修掉——前者在 173 验证栈上是可复现的用户可见缺陷，
后者是「已取消/已过期仍可写」的一致性缺口。
