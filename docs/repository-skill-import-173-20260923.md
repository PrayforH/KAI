# 仓库 ZIP 技能导入修复（173）

代码提交：`85e50f60879fe47e336403564d10008fe81f9c1b`。

## 原因与行为

用户下载的 `archify-main.zip` 包含两个入口：

- `archify-main/archify/SKILL.md`：要安装的技能。
- `archify-main/.agents/skills/archify-review/SKILL.md`：仓库维护用技能。

原导入逻辑已经支持任意深度子目录，但要求整个 ZIP 恰好一个 SKILL.md，因此把上述仓库包误判为不能导入。修复后优先选择非隐藏目录中的唯一入口；如果只有隐藏目录入口，仍支持导入。多个正式技能不猜测选择，错误中列出候选路径并要求单独打包；无入口时明确提示支持子目录。入口文件名大小写兼容。

导入文件范围仍限定为所选 SKILL.md 的父目录，不包含旁边的仓库维护技能、网站及仓库文档。路径、符号链接、凭据、压缩大小和文件数量校验保持生效，不执行上传内容。

## 验证

- 原始 `/Users/xiaokai/Downloads/archify-main.zip`（22,231,110 字节）可直接导入，技能名称 `archify`，附件 218 个，包含 `assets/template.html`，未包含 `archify-review`。
- 单元与 API 回归：91 passed，覆盖隐藏维护技能、深层目录、大小写、多个候选、无入口、二进制资产、大技能安装与再导出、原有 Skill Creator 接收流程。
- Ruff 和 git diff --check 通过。
- 173 真实 API 使用原始 ZIP 安装到临时草稿 `draft_64c9e9a4f9644fc2ae2fed040df6ecd1`，保存为 r2；导出 Agent 包后逐一比对 218 个附件字节完全一致，未夹带维护技能。临时草稿已删除，未修改已有用户草稿。

## 部署

173:3302 API 更新为 `kai/axis-api:evolution-builder-85e50f60`；Worker 与 Web 镜像保持原版本。更新前无活跃运行，更新后三个服务 healthy，API health 与 Web login HTTP 200。

备份位于 `/data/agent-studio-evolution-20260920/backups/builder-import-85e50f60/`。回滚只需恢复 API 镜像 `kai/axis-api:evolution-builder-35476952`，无数据库迁移。174/develop 与 173:3301 未部署修改。
