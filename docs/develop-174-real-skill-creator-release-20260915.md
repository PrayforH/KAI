# 174：真实 Skill Creator、模型初稿与入口统一

2026-09-15 已部署应用提交 `6a4f6181936519b9c62864db0b9373611310217f`，develop 已推送。

## 最终行为

- 构建助手创建或更新 Skill，走真实 Worker 预览运行，加载平台固定版本的 Anthropic `skill-creator` 完整包。需要成功的技能加载和官方打包调用、真实 `.skill` 产物及测试用例，才返回可应用的修改建议。
- Skill、附加文件、评测用例和工具绑定一起进入 DeepAgents 差异审阅。应用使用现有草稿 CAS，不直接发布。业务测试与基线评估没有自动运行，不报告虚构评分。
- 按需求创建初稿实际调用所选模型，生成名称、描述、系统提示词和任务契约；从当前可用 Skill 目录推荐最多五项并说明理由。选中后审阅、安装，保留包来源及版本。
- 删除前端虚构的 Skill Creator 目录行，停用 OpenAI 离线适配的 `skill-authoring-quality`，保留“Skill Creator（Claude 官方）”及其创建快捷操作。
- 默认 Lead 由平台目录装配完整原版，不再装配旧适配版和源码中的简化占位文件。174 当前默认版本为 `1.0.2+platform.99dfe677`。历史发布快照保持不可变。
- 初稿不再被前端附加“禁止联网”条件，也不静默过滤 MCP。模型、联网、知识库和部署环境配置沿用原值。

## 验证

真实创建运行：`run_607f4d1988594d278ad50e5e700839d1`，终态 `succeeded`。

- 原版来源版本：`41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f`。
- 事件记录包含 `Skill(skill-creator)`；实际执行 `/app/platform-skills/skill-creator` 下的 `python -m scripts.package_skill`。
- 已生成 `source-review.skill`、`evals.json`、SKILL.md 与来源核验模板。生成前后草稿修订不变；审阅应用后的导出文件与预览逐项一致；过期建议返回 409、非所有者返回 404。
- 真实初稿验收两次：模型生成办公文档助手，推荐 Word、Excel、交付核验等包；选择后的固定来源安装和导出差异一致。临时草稿均已清理。
- 174 旧适配版草稿使用核对结果为空。部署后平台包目录、能力目录及当前 Lead 快照均验证为只有一个官方创建技能；完整包包含打包脚本和评测查看器。
- 后端原功能相关测试 76 项通过；入口统一及平台目录、默认 Lead、真实创建编排相关测试 52 项通过。Ruff、Pyright 通过。
- 前端全量测试 599 项通过、1 项跳过；入口统一后相关测试 27 项通过；Next 生产构建通过。上线 Web 返回 200，16 个入口分块和代码视图主题分块可加载。
- 浏览器自动导航工具超时，本次没有截图验收；以上前端验证来自组件测试、生产构建与上线资源检查。

## 发布与回滚

API、3 个 Worker、quality-sync 与 Web 均 healthy，重启计数均为 0。

| 服务 | 镜像 | 镜像 ID |
| --- | --- | --- |
| API / Worker / quality-sync | `kai/axis-api:develop-20260915-6a4f618` | `sha256:aa6ebfcf672fc7b6c8c185297ef08442db46dfe8de3f48658de6675755a6529a` |
| Web | `kai/axis-web:develop-20260915-6a4f618` | `sha256:c37f717e6d0df6f6cadcc03e30234194d07cf19b7ec3b7e6acc270a3119afd54` |

Web 容器：`axis-web-develop-20260915-single-skill-creator`，端口 3501。

最终发布目录：`/data/kai-develop-20260915-single-skill-creator`。其中保留受保护的原容器配置、数据库备份、完整 compose 叠加链、`rollback-api.sh` 和 `rollback-web.sh`。回滚目标为上一版 `ffced21`；原技能接入发布记录及更早回滚保留在 `/data/kai-develop-20260915-real-skill-creator`。

发布中遇到生产数据库预览版本要求非空 Agent 身份，已修复为绑定源 Agent 的独立预览版本；并增加旧修订拒绝及模型生成成功后才创建 Agent 身份的保护。
