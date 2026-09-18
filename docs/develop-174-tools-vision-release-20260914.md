# 174 联网工具校验修复与 Flash 视觉配置发布

日期：2026-09-14。应用源码提交：`bc2eb67def81fa8ae2e04c38555b0fad5a70b5de`，已推送 develop。

## 发布范围

- 包含 develop 已合入的 WeKnora 知识库、DeepAgents 项目导出、样式修复和非 lead-agent 顶部版本选择。
- 修复用户或平台关闭联网时，已发布/试跑智能体因 `runtime builtin tools differ from the published tool directory` 整体运行失败。
- 更新 174 现有 Flash 模型配置为支持视觉输入；未合入原工作区的 Tavily MCP 全局下线改动。

## 故障与修复

原失败运行：`run_b9120abe435241b0a858ef3e6ffe2b66`，智能体 `govdoc-writer-agent`，草稿版本 4。

该用户的有效联网开关为关闭。Manifest 和不可变工具目录均包含 WebSearch、WebFetch；ToolResolver 按用户配置去掉二者，目录校验却要求内置工具集合完全相等，因此在模型调用前失败。

ToolResolver 现在显式记录因联网设置禁用的内置工具。目录校验仅允许已记录的 WebSearch/WebFetch 缺失，不修改发布快照、不重新启用联网；其他工具缺失、意外新增仍失败，错误消息列出差异。用户启用联网后，工具仍经 harness-web → UserWebClient 使用当前用户的提供方与凭据。

## 模型配置

用户说明新 Flash 与现有 Flash 使用同一模型映射，因此保留稳定路由和上游模型 ID，避免破坏已有绑定。

| 字段 | 发布后值 |
| --- | --- |
| 展示名称 | DeepSeek-V4.1-Flash |
| 路由 ID / 上游模型 ID | deepseek-v4-flash |
| 类型 | vision |
| 能力 | streaming、tool_use、vision |
| 协议 | anthropic_compatible |
| 地址 | http://172.20.109.112:31300/v1 |
| 路由配置版本 | 4 |

保留原地址、认证与凭据，Pro 配置未改变。网关 `/models` 当时未提供单独的 V4.1 ID；展示版本依据用户提供的映射，不能将目录中的旧别名当作独立的供应商版本证明。通过现有别名实际发送红/蓝双色 PNG，网关返回 200 并准确识别左右颜色；保存配置后，再通过 ModelConfigurationService.complete_text 图片链路验证成功。

## 部署与回滚

- 正式 Web：http://172.20.109.174:3501；API：8800。旧 3301 服务保持原状。
- API/三个 worker/quality-sync：`kai/axis-api:develop-20260914-bc2eb67`。
- Web：`kai/axis-web:develop-20260914-bc2eb67`。
- API 镜像 ID：`sha256:5fdaf848d219542cd0b57b505bf3c5ee8bcc34a91ba9b12c25ace6e53ee1ee4c`。
- Web 镜像 ID：`sha256:7791383119e610702d855b68160df02b6fb000ec6c2d405a3df3138984d646b4`。
- 在 174 原生构建，后端继承上一版已验证依赖镜像、离线更新完整应用源码/agents/skills/migrations；SDK 0.2.152、packaging 26.2、pip check 和办公 Node 依赖检查通过。前端使用与 package-lock 一致的 Linux 依赖缓存完成 Next.js 生产构建。
- 保留完整 Compose overlay 链和原应用环境变量；迁移版本为 0032。
- 新备份目录：`/data/kai-develop-20260914-tools-vision`，含数据库备份、私有容器配置、构建源码及 rollback-api.sh / rollback-web.sh。
- 回滚目标是本次发布前的 `knowledge-develop-20260914-efb6eb0` 镜像；旧 Web 容器保留且停止。应用回滚不自动覆盖模型配置或业务数据库。

## 验证

- 相关单元测试 63 项通过，含 eager/on_demand、用户/平台关闭联网、重新启用和异常工具缺失检查；变更文件 Ruff、Pyright 通过。
- 从 174 读取原始失败版本，移除禁用原因记录可重现差异；保留记录后校验通过且联网工具仍不可用。
- 正式服务完整试跑 `run_10f714c45de94b19a64e884f3829104f`，状态 succeeded；同一草稿、同一用户联网关闭设置。
- API 检查通过；RAG/hybrid 知识库创建、配置、文档代理与访问隔离通过，临时知识库已删除。
- DeepAgents ZIP 下载成功，项目结构、0.7.13 依赖固定和 agent.py 语法验证通过，临时导出草稿已删除。
- 正式 Web 首页及 16 个 JavaScript 分块正常加载，构建产物包含 DeepAgents 入口与非 lead-agent 顶部版本选择条件。
- API、三个 worker、quality-sync、正式 Web 共六个容器均 healthy，重启次数均为 0。
