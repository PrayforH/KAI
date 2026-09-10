# 舆情分析智能体 Codex 分支清理记录

- 日期：2026-09-10
- 环境：`172.20.109.173`
- 范围：`public-opinion-agent` 的 codex 分支（0.3.13 之后全部）+ 对应会话历史与文件
- 留底目录（173）：`/data/agent-studio-builds/purge-opinion-20260910/`

## 1. 清理范围（执行前清点）

判据用**运行时**而不是版本号字符串——`'0.3.6' > '0.3.13'` 在字典序下为真，
且 0.3.6/0.3.7/0.3.8 在 SDK 与 codex 两种运行时下**各存在一份**（owner 不同）。

| owner | 删除版本 | 删除会话 |
| --- | --- | --- |
| `user_1c16a…`（主账号） | 0.3.14、0.3.15、0.3.16、0.3.17、0.3.18、0.3.19、0.3.20、0.3.22、`preview-…-18-…` | 35 |
| `developer` | 0.3.6、0.3.7、0.3.8（均 codex） | 5 |
| **合计** | **12 个版本** | **40 个会话 / 97 个 run / 24,769 条事件 / 130 个产物** |

保留：主账号 `0.3.5`–`0.3.13`、`developer` 的 `0.3.5`（全部 claude-agent-sdk）。

0.3.15 一并清理：它是 codex 迁移过程中的畸形中间态（SDK 运行时却钉 codex 路由）。

## 2. 留底（删除前导出，173 本地）

```
backup_versions.csv       84M   全部 agent_versions 行
backup_sessions.csv       32K   40 个会话行
backup_runs.csv          197K   97 个 run 行
backup_run_events.csv     19M   24,769 条事件
backup_artifacts.csv      68K   130 条产物索引
backup_drafts.csv        124K   3 个草稿行
```

## 3. 执行

### 3.1 会话与文件（走平台自身的生命周期通道）

每个会话提交一个 `scope=session, kind=delete` 的 `POST /v1/data-lifecycle/jobs`，
由 `DataLifecycleController` 执行五个适配器：

| 适配器 | 覆盖 | 结果 |
| --- | --- | --- |
| object-store | 工作区与产物对象存储文件 | 40/40 succeeded |
| sdk-session | Claude SDK 会话存储 | 40/40 |
| memory | 会话相关记忆 | 40/40 |
| langfuse | 链路追踪 | 40/40 |
| postgresql | 会话/运行/事件/产物行 | 40/40 |

选 `session` 作用域而不是 `agent`：AGENT 作用域按 `agent_name` 匹配，会连
0.3.13 及更早的会话一并删除，超出本次范围。

### 3.2 版本行与评分

事务内删除 12 个版本行，并清理**孤儿** `quality_scores`：

```sql
DELETE FROM agent_versions v USING purge p
 WHERE v.name='public-opinion-agent' AND v.owner_user_id=p.owner_user_id AND v.version=p.version;
DELETE FROM quality_scores q
 WHERE q.agent_name='public-opinion-agent'
   AND NOT EXISTS (SELECT 1 FROM agent_versions v
                    WHERE v.name=q.agent_name AND v.version=q.agent_version);
```

`quality_scores` 只有 `agent_version` 没有 owner，**不能按版本号直接删**——那样会连带
删掉被保留的 SDK `0.3.6/0.3.7/0.3.8` 分数。改为按「删完是否还有版本行」判定孤儿，结果删除 558 条。

### 3.3 草稿悬挂引用

三个草稿中两个仍指向被删版本（`0.3.22`、`0.3.8`）且 `runtime=codex-app-server`：

- `runtime` 改回 `claude-agent-sdk`，去掉 codex 专有的 `model.reasoningEffort`
- 清空 `publishedVersion` / `publishedHash` / `publishedPackageHash`（不再指向已删版本）
- 其中一个的 `model.routeId` 仍是 `codex-deepseek-v4-flash`（即 0.3.15 那种畸形组合），改回 `deepseek-v4-flash`

## 4. 验证

| 项 | 结果 |
| --- | --- |
| 版本下拉数据源 `/v1/agents` | 仅 `0.3.5`–`0.3.13`（全 SDK） |
| 渲染页面全文扫描 | 不含 `0.3.14`–`0.3.22` 任一版本号 |
| 保留版本可用性 | 新建会话 + run（`0.3.13`）→ `succeeded` |
| 孤儿数据 | 无 session 的 run / 无 run 的事件 / 无 run 的产物 / 无版本行的评分 均为 0 |
| 生命周期任务 | 40/40 succeeded |

## 5. 边界说明

- 对象存储文件由 `object-store` 适配器按其契约删除（40/40 报告成功），未做逐对象人工复核。
- codex 运行时与 `codex-deepseek-v4-flash` 路由**保留未删**：前者是平台能力，后者昨日刚按事故处置恢复，
  且路由删除/禁用现已受「被已发布版本引用则拒绝」保护；确认无版本引用后可另行清理。
