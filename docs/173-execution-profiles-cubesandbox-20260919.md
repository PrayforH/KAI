# 173 运行配置的 Execution Profile 补齐 cubesandbox（2026-09-19）

## 1. 现象与根因

智能体「高级运行配置」的 Execution Profile 下拉仍是旧清单（local×2 + e2b-public-egress +
gvisor-production），没有 cubesandbox 档。根因：下拉读取的是**存量租户目录**
（`capability_catalogs`，tenant `local`，rev 79），而 `cubesandbox-private` /
`cubesandbox-egress-enforced` 是后来才加进代码默认目录的——存量目录不会自动长出新档。

## 2. 变更（目录 replace，rev 79 → 80，updatedBy=system:cube-unify-20260919）

| profile | 处理 | 理由 |
| --- | --- | --- |
| local-development v2 / isolated-default v3 | 保留（含各自 MCP 白名单） | 本机档仍可用 |
| e2b-public-egress v1 | **enabled=false** | 173 无外网，e2b 不可用；保留记录避免破坏既有引用 |
| gvisor-production v2 | **enabled=false** | 173 无 k8s/gvisor；同上 |
| **cubesandbox-private v1** | **新增**，enabled，allowedMcp=[sentiment_query_mcp] | 与 174 的同名档一致 |
| **cubesandbox-egress-enforced v1** | **新增**，enabled，allowedMcp=[]（出口强制语义） | 与 174 一致 |

未新增 opensandbox-gvisor（后端已统一 cube，避免混淆）。PUT 走正式 API
（`PUT /v1/studio/catalog`，服务端完成目录级校验后落库）。

## 3. 验证

`GET /v1/studio/capabilities` 返回 6 档，cubesandbox 两档 enabled=true。前端界面需**硬刷新**。

注意：已发布快照如果钉着被停用的档（e2b/gvisor），其 Run 会被拒绝
（`execution_profile_sandbox_provider_not_enabled`）——这是预期行为，这类 Agent 需要重新
发布到 cubesandbox-private。173 当前 `deployment_snapshots` 为空，无受影响对象。
