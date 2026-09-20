# Agent Studio 持续改进控制面：实现与 173 验证记录

日期：2026-09-20。代码分支：`auto/agent-evolution`，基线 `4a43a400`。本轮在独立 worktree 开发，未带入原工作区其他任务的未提交改动。原设计：[智能体持续改进改造方案](https://my.feishu.cn/docx/OcMcdn0eqo4vyXxTwazczJpAnhc)。

## 交付结论

第一阶段工程闭环已实现并在 173 的独立实例验证：质量事实 → 固定验证集 → 冻结基线 → 白名单候选修改 → 基线/候选真实运行 → 服务端对照报告 → 人工审批 → 发布 → 观察/经验 → 回滚。

真实模型实验使用 3 个合成固定输出用例，基线 0/3、候选 3/3，零回归、无成本缺失。它证明闭环与发布门禁能运行，不证明业务能力、复杂安全行为或泛化收益。没有用模拟运行时分数替代真实模型证据。

验证站点：http://172.20.109.173:3302 。API：http://172.20.109.173:8802 。原 3301/8800 实例、镜像与业务数据保持不变。

## 实际改造

| 层级 | 本轮改动 | 主要位置 |
|---|---|---|
| 质量事实 | trace 可空、未知成本不按零处理、终态重放幂等、后续关联 trace、反馈独立于 trace | `quality/`、`storage/quality_repository.py` |
| 可信评测 | 稳定失败分类、费用/Token/工具路径断言、输出 JSON 断言、split/family、独立 trial、内部 preview 执行 | `evals/` |
| 任务控制 | 冻结 Spec/数据集/策略及哈希，任务预算、过期/取消、PostgreSQL CAS、所有者隔离 | `evolution/`、迁移 `0035` |
| 候选 | 只允许 systemPrompt 或已有 Skill instructions；精确匹配原文；保留安全章节；不修改来源草稿 | `evolution/service.py` |
| 对照 | 使用现有 EvalController、耐久队列和真实运行；客户端不能上传分数代替评测 | `evolution/service.py`、`evals/service.py` |
| 审核与发布 | 人类 JWT 会话、候选/报告/策略哈希绑定、审核有效期、统一发布入口防绕过 | `evolution/api.py`、`application/agents.py` |
| 经验与观察 | 来源、适用条件、审核/撤销、过期、私有范围；发布版本运行与反馈汇总 | `evolution/models.py`、`service.py` |
| 界面 | 任务、diff、实验、报告、审核、发布、个人回滚；运行控制台选中审核候选及历史快照回滚 | `evolution-workspace.tsx`、`agent-operations-workspace.tsx` |

新增的是现有 Studio 上的控制面，没有替换运行时、评测队列或部署系统。评测 preview 为内部能力，普通 API 请求不能自行打开 previewExecution。正式发布门禁可通过 manifest/package 哈希复用同一候选的已通过评测证据。

## 173 部署边界

工作目录 `/data/agent-studio-evolution-20260920`，Compose project `agent-evolution-173`。独立 API、worker、Web、PostgreSQL、Redis、MinIO。新库从空库升级至 `0035 (head)`；新对象存储桶 `evolution-validation`。

仅为模型调用复制现有模型 catalog 和模型控制面的加密连接；没有复制现有用户、业务会话或运行记录。环境文件仅 root 可读，凭据未入库、未写入本报告。测试账号凭据保存在验证目录的 `test-account.json`，仅 root 可读，用于接手查看所有者范围内的验证记录。JWT 加密材料在本验证环境复用，以解密模型连接，不能据此宣称完成了密钥隔离。浏览器 Cookie 使用独立前缀，避免覆盖原站登录。

API/worker 镜像 `kai/axis-api:evolution-20260920`，镜像 ID `sha256:6a0568f663515fcc5a94d58a3cb3b4eb278811d64d5959a98295e5e69760ff43`。

Web 镜像 `kai/axis-web:evolution-20260920`，镜像 ID `sha256:30a1491b53c0217a41b48f9f4a1809db230830139aee48d1da0e08d010e4c436`。

构建基于 173 已有的匹配依赖镜像，前端保留 Linux 原生依赖；这些是验证镜像，不替代生产标准构建。部署与停止说明见 `deploy/evolution-validation/README.md`。

## 真实模型验证证据

测试智能体 `evolution-smoke-165948`；运行时 `claude-agent-sdk`，使用环境配置的真实模型连接。仅合成固定输出，无外部写操作或工具调用。

| 项目 | 结果 |
|---|---|
| Job | `evo_3cc4b19fd62e5236875766c197009ee0` |
| Candidate | `candidate_b221f1348cbef48bd92a0739` |
| 正式候选版本 | `0.1.0+evo.b221f1348cbef48b` |
| 基线 EvalRun | `eval_run_c39442e220d5468d95421fa65bad86e8` |
| 候选 EvalRun | `eval_run_8d278b7f0f53451d923041221afcad01` |
| 固定集 | 3 用例，1 组配对试验；validation split |
| 基线 / 候选 | 0/3 → 3/3；改善 3、回归 0、未解决 0 |
| 基线 / 候选成本 | $0.027185 / $0.028450，合计 $0.055635 |
| 成本缺失 | 0 |
| 报告哈希 | `f6237514692590f363c5cc99b32c3ab4faa4c02ce27860f37b1d2031a9fc8f2c` |

本试验把输出约定从 OLD 改为 FIXED，评分器预先固定要求 FIXED。三个分类标签只用于走通数据模型，不是三个真实业务能力的金标。只有 1 组试验、3 个合成用例，不能报告统计显著性或业务成功率提升。

## 门禁、灰度、回滚与持久化

- 未审批直接发布返回 409；提交错误报告哈希审批返回 409。
- 审核通过后正式发布成功；无样本观察明确显示“不能判断业务收益”。
- 发布后创建一次真实运行，观察记录为成功 1/1、成本缺失 0、反馈 0。
- 有来源的经验完成记录与审核；没有自动写入个人记忆。
- 正式候选通过环境评测门禁；完成 canary 首次 100% 部署，再配置 90%/10% 双快照路由，随后回滚至历史已验证快照 100%。两个快照属于同一候选、不同测试配置，仅验证路由和回滚机制。
- 浏览器已验证持续改进页、候选 diff/报告/审核/观察、环境部署链接。运行控制台显示正式候选版本，避免选择旧草稿版本。
- 从浏览器执行个人版本回滚后，候选状态 `rolled_back`、任务 `completed`；API 目录确认个人 current_version 回到 `0.1.0`。
- 重启隔离 API/worker 后，报告哈希、回滚状态、经验和观察记录保留。未执行运行中的 worker 强杀恢复压测。
- PostgreSQL 真实仓储验证：聚合对象往返、跨 owner 不可读取、并发 CAS 恰好一个写入成功。
- 原 API `/healthz` 与原 Web `/login` 返回 200，原容器未重启；隔离 API `/healthz` 返回 200。

个人版本回滚不会自动改变环境路由。验证结束时个人默认版本为基线，canary 环境仍指向已验证的候选快照；这两种指针独立。

## 测试与质量检查

| 检查 | 结果 |
|---|---|
| 后端全量本地测试（最终代码） | 1717 passed，6 skipped，5 warnings，158.69 秒 |
| 进化/评测/质量专项 | 45 passed |
| 前端 Vitest | 715 passed，1 skipped |
| Next 生产构建 | 通过，含新持续改进路由 |
| Ruff `src tests` | 通过 |
| `git diff --check` | 通过 |
| 新增控制面与关联专项 Pyright | 0 errors |
| 全仓 Pyright | 未通过；相同依赖解释器下基线 740 项、改动后约 637 项，旧 Makefile 门槛 284 已失配 |

全仓类型检查债务仍需单独治理，不能将专项通过描述为全仓类型检查通过。测试用 deepagents optional extra 安装完整依赖，并在执行时取消本机代理环境变量，避免本地 SOCKS 配置干扰测试。

复现主测试：

```sh
uv sync --group dev --extra deepagents
env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy uv run --extra deepagents python -m scripts.run_local_tests
uv run --extra deepagents ruff check src tests
cd web/harness-console
npm test -- --run
npm run build
```

机器可读脱敏结果在 `docs/validation/2026-09-20-evolution-173-results.json`，包含发布拒绝、观察、环境路由、回滚与试验标识。

## 实测发现与修复

1. 新数据库 preview AgentVersion 要求 agent_id 非空，创建 preview 时补入来源智能体 ID。
2. 旧评测器用换行拼接流式消息片段，可能把 FIXED 解析成 F\\nIXED 导致假失败。改为同一消息内顺序拼接，并保留最终消息回退，补充碎片化文本/JSON 测试。
3. 初始验证桶缺失导致 artifact finalization 重试，增加幂等桶初始化脚本，后续真实运行正常结束。
4. 第一轮失败试验完整保留；修复后创建新任务/新试验验证，没有修改旧试验结果来获得通过。
5. 原来源草稿仍为基线，运行控制台此前可能部署旧版本。新增按 Job/Candidate 查询服务端已发布版本的路径，不接受客户端自报哈希作为部署依据。

6. 隔离 worker 最初继承 API HTTP 探针导致错误的 unhealthy 状态，已改为与原部署一致的 worker 进程探针；任务执行日志正常。

## 已知边界和后续工作

- 没有启用自动优化器或 RL；受限 patch 可由人工或后续 proposer 提交，审批发布仍属于人工。
- 预算预留与已观察费用停止调度不是供应商计费硬上限；未知费用阻断审批。未恢复项目已取消的费用/Token 执行额度。
- validation 冻结、split/family 是基础设施；holdout 独立访问控制、泄漏检测、专家金标和统计验收尚未完成。
- 经验仍为所有者私有记录，未自动注入 Prompt/记忆，未扩大团队共享。
- 来源草稿不自动采用候选；下一轮改进需显式建立新发布基线。多个同一智能体的活跃任务会受到发布所有权门禁约束，应结束或取消旧任务后推进下一轮。
- 质量事实收集依赖现有终态钩子，本轮未增加全局自动补采。发布中断可在审批有效期内重试；过期后遗留 releasing 状态仍需人工恢复。
- 已验证的是个人智能体。团队共享、长期在线实验、运行中故障恢复和业务收益需要后续单独验收。

建议先选一个实际业务智能体建立 30–50 条专家标注回归用例，再接可替换的优化器提案接口。遵循原方案的开源借鉴原则：借鉴优化算法与评测方法，继续复用本项目的运行、权限、发布与审计体系。
