# 舆情分析智能体 Codex 路由中断 · 事故分析与处置记录

- 日期：2026-09-10
- 环境：`172.20.109.173`
- 影响：`public-opinion-agent@0.3.14`–`0.3.22` 全部 run 立即失败
- 处置 tag：`route-guard-20260910-r2`

## 1. 现象与根因

`public-opinion-agent` 从 0.3.14 起改用 `codex-app-server` 运行时，并把模型钉在
`codex-deepseek-v4-flash`（`apiFormat=openai_compatible`，模型
`deepseek-v4-flash-vision-exp`）。0.3.13 及更早版本用 `claude-agent-sdk` +
`deepseek-v4-flash`（`anthropic_compatible`）。

**2026-09-09 16:23，有人在「设置 → 模型管理」中删除了
`codex-deepseek-v4-flash` 路由**（审计：`studio.model_configuration.delete`），
同批操作还把 `glm-5-2` 换成 `glm-5-3-flash`。此后这 9 个已发布版本失去模型路由：

```
ConflictError: task model route is unavailable in the control plane: codex-deepseek-v4-flash
```

要点：这些版本**不可变**，会话又钉住版本，所以故障是「已发布版本 — 路由」这条
引用被静默打断，而当时的删除检查只覆盖草稿与 agent 级绑定，**看不到已发布版本**。

## 2. 协议问题的实质

表面看是「Codex 用 OpenAI Responses 协议，Claude Agent SDK 用 Anthropic 协议，
两者不一致」。实测网关后结论不同：

```
POST http://172.20.109.112:31300/v1/responses          → 401（存在，需鉴权）
POST http://172.20.109.112:31300/v1/messages           → 401（存在）
POST http://172.20.109.112:31300/v1/chat/completions   → 401（存在）
```

**网关三种协议都能出，同一个上游模型不需要两条路由。** 真正的建模缺陷是：

1. 目录把「一个模型」建模成「一条路由 + 一个 `apiFormat`」。同一网关同一模型因此
   被登记成两条路由（`deepseek-v4-flash` 与 `codex-deepseek-v4-flash`），端点与
   凭据都要各存一份，任一条消失都会打断钉在它上面的版本。
2. `claude-agent-sdk` 的能力声明里写了 `openai_compatible`，但 SDK 解析路由时
   **完全不看 `apiFormat`**（`resolve_runtime` 只按 route_id 取 `base_url`，协议由
   SDK 自身决定）。也就是说这条声明名不副实，会让人误以为「SDK 也能吃 OpenAI 路由」。
3. 运行时与路由的绑定是共享的：`registry_codex_runtime.py` 的注释已指出，agent 级
   路由绑定同时被 Claude 版本使用，套用会让另一运行时静默失效，所以 Codex 路径
   只能绕开它（`apply_agent_binding=False`）并强制要求 `openai_compatible`。

## 3. 处置

### 3.1 恢复（解封 0.3.14–0.3.22）

1. 重建凭据：凭据密文的 AAD 绑定了 `tenant\0owner\0reference`，**不能直接复制**
   另一条路由的密文。在 API 容器内用平台自身的 `McpCredentialCipher` 解密再按新
   reference 重新加密写回，明文不出进程、不落日志。
2. 通过 `PUT /v1/studio/catalog/modelRoute/codex-deepseek-v4-flash` 重建路由：
   `openai_compatible`、`baseUrl=http://172.20.109.112:31300/v1`、
   `models=[deepseek-v4-flash]`、`authScheme=bearer`、`enabled=true`。
3. 验证：
   - 模型连接测试 `POST /v1/studio/models/codex-deepseek-v4-flash/test` → `ok:true`，787 ms（Responses 协议确实可用）
   - 原失败会话重跑 → 先 `succeeded`，再用另一问题复测仍 `succeeded`

### 3.2 防复发

- `CatalogImpact` 新增 `publishedAgentVersions`：影响面分析现在会列出钉在该路由上的
  已发布 `name@version`，管理界面删除前即可看到真实影响。
- `delete_model` 与 `disable` 都拒绝在仍有已发布版本引用时执行（软禁用同样会导致
  运行时「路由不可用」，破坏力与删除一致）。
- 新增 registry 投影查询 `route_references(tenant_id, route_id)`，按存储的 manifest
  定位引用，避免全量加载版本包。

实测拦截信息示例：

```
409 conflict
Rebind or update these references before deleting the model:
  draft:draft_cc979f…, published:agent-111@preview-…, published:public-opinion-agent@0.3.22, …
409 conflict
Published Agent versions still pin this model route; publish a rebound version
or remove those versions first: archive-assistant-agent@0.1.1, …
```

## 4. 后续建议

| 方案 | 说明 | 代价 |
| --- | --- | --- |
| A. 一模型一路由 | 目录只保留每条「端点 + 模型」一条路由，协议由运行时决定（网关都支持，运行时只需选 `/v1/messages` 或 `/v1/responses` 路径） | 需要改 ModelRoute 模型与两个运行时的路由解析；`delete_model`/`disable` 的语义不再被协议复制品污染 |
| B. 双路由但纳入种子与保护 | 保留协议专用路由，但把 `codex-*` 纳入部署种子，并对「被引用路由」加保护 | 改动小；仍保留重复端点/凭据与两条路由漂移的风险 |
| C. 舆情智能体迁回 SDK | 该 agent 的工具集与 0.3.13（SDK 版）完全一致，可直接发布新版本到 `claude-agent-sdk` + `deepseek-v4-flash` | 逐版本回迁；旧会话仍钉在 codex 版本，需保留路由或重绑会话 |
| D. 修正能力声明 | 从 `claude-agent-sdk` 的 `modelApiFormats` 去掉 `openai_compatible`（或让 SDK 真正支持），避免校验放行实际不可用的组合 | 小；消除误导性校验 |

推荐顺序：**C（新版本回迁，减少长期分叉）+ A（削掉协议复制品）+ D（消除声明误导）**；
在 A 完成前，B 的保护是必要兜底，现已落地。
