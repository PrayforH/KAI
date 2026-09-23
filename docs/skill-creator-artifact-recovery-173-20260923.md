# Skill Creator 产物接收与进度修复（173）

代码提交：`35476952a61a01991e0a06dbaf8a5eaa00b2a667`。

## 故障与修复

运行 `run_ffe10c781c9341708ed517893c45eaf9` 于 2026-09-23 09:50:22–09:51:52 UTC 成功完成。它发布了 `html-report-builder.skill` 和 `html-report-builder-evals.json`，但 Builder 接收端只接受 `evals.json`，误报“未发布测试用例”。上游打包脚本不把 evals 装入技能包，单独发布的测试文件确实存在。

- 接收端接受 `evals.json` 或当前技能名限定的 `<skill>-evals.json`，拒绝重复/歧义文件及其他技能的 JSON，保留大小、JSON 结构和有效测试输入校验。
- 创建提示明确要求 publish_artifact 的展示名称参数，减少生成与接收约定漂移。
- Worker 作者运行向 Builder SSE 转发读取、编写、校验、打包、发布等实际工具阶段，仅显示固定阶段文本，不暴露工具输入。等待事件时推进序号，避免一直从 0 等待造成重复唤醒。
- 创建阶段显示“正在生成待审阅建议”；完成后提示审阅，不再保留模型先前的“等待确认才创建”回复。通用报告技能的数据来源/联网选择可作为运行输入；缺少关键业务输入才追问，追问时不同时发起创建。
- 草稿仍仅在用户应用建议后修改。

## 验证与恢复

- Worker Skill Creator 单元测试与 Builder API 集成测试：29 passed。包含限定文件名、无关/重复文件、损坏/超大/无有效用例的 JSON、真实进度顺序、事件序号、超时取消、SSE 转发、预览不写入和应用流程。
- Ruff 与 git diff --check 通过。
- 下载原运行两个产物，分别验证 SHA-256，使用修复后的接收逻辑和现有安装校验合并 3 个测试用例。
- 173 部署后，直接调用部署版本的产物接收逻辑读取原运行文件成功；对 `draft_9e9a7e44a7234c4eb6c3ad3005493c46` 调用现有 builder-project-diff 返回成功，预览包含技能的 5 个文件（含 SKILL.md）。操作前后草稿均为 r4，spec 完全一致。
- 没有重新运行生成模型，也没有自动安装技能或执行业务测试。恢复包保留在本地 `/tmp/skill-creator-recovery-run_ffe10c78/html-report-builder-with-evals.skill`，包含原始技能文件和恢复的 evals/evals.json。

## 部署

- 173:3302 API：`kai/axis-api:evolution-builder-35476952`。
- Worker 保持 `kai/axis-api:evolution-builder-f4af9b51`；Web 保持 `kai/axis-web:evolution-builder-899ae75c`。本次仅修改 API 控制面；Worker 收到的新任务使用 API 生成的新提示。
- 更新前活跃运行数为 0，镜像导入检查通过；更新后三个容器 healthy，API health 与 Web login HTTP 200。
- 备份：`/data/agent-studio-evolution-20260920/backups/builder-creator-35476952/`，包含更新前 Compose 与 API 源码版本。
- 如需回滚，只将 API 镜像恢复为 `kai/axis-api:evolution-builder-f4af9b51` 并重建 API；不用修改数据、Worker 或 Web。
- 174/develop 与 173:3301 未部署修改。
