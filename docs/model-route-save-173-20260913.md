# 173 模型配置保存 500 修复

- 日期：2026-09-13（北京时间）
- 环境：172.20.109.173，agent-studio-173-api-1。
- 现象：编辑已有模型、修改实际模型名，PUT /v1/studio/models/{route_id} 返回 500。

## 原因

路由 ID 是关联智能体、发布版本与凭据的稳定标识；前端编辑时显式禁用该字段，并使用原 routeId 提交。实际模型名称单独存储，允许更新。

500 来自 configure → catalog.upsert → impact → list_all_for_tenant → _load_draft。两条历史草稿的关系列与 JSON payload 元数据不一致，触发 Corrupt Agent Draft persistence envelope：

| 草稿 | 关系列 revision | JSON revision |
| --- | --- | --- |
| draft_1eb24f48d77c449e87aad16673eb9a52 | 21 | 20 |
| draft_cc979f75309349e5b45f67ce15ecf77b | 87 | 85 |

两条记录的 updatedAt 同样滞后；tenant、owner、draft ID、name 均一致。此次未确定最初产生不一致的写入来源。

## 处置与验证

1. 使用 pg_dump 备份 agent_drafts 数据到服务器 `/data/agent-studio-repairs/model-route-20260913/agent_drafts.before.sql`，目录和文件通过 umask 077 创建。
2. 事务内锁定两条记录，核对预期版本，只把 payload.revision、payload.updatedAt 同步到关系列值；保留 spec 内容，并通过现有 _load_draft 校验后提交。
3. 重新加载数据库全部 18 条草稿，完整校验通过。
4. 在外层回滚事务内执行线上 ModelConfigurationService.configure：原模型 deepseek-v4-pro 保存成功；改为截图中的 deepseek-v4.1-flash-expires-on-0910 同样保存成功。测试使用现有路由的地址、协议及已保存凭据，apiKey 留空。
5. 回滚验证事务，确认模型目录完整等于验证前状态，revision 仍为 68。未执行上游推理连接测试，未切换生产模型。

## 配置说明

生产路由 deepseek-v4-pro 当前连接 `http://172.20.109.112:31300/v1`，协议为 anthropic_compatible。截图选择 OpenAI 后，表单变成 `https://api.openai.com/v1` 和 openai_compatible；这与已保存的网关配置不同。只换模型时应保留正确网关和运行时匹配的协议。此次修复解决保存故障，不代表截图中的模型已获上游支持。

无需应用重启或镜像发布。未修改路由 ID 的稳定标识规则。
