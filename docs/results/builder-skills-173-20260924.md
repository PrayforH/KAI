# Builder 技能权限与模型意图修复（173，2026-09-24）

版本 `58518b27`，分支 `auto/agent-evolution`。API、Worker、Web 已部署，健康检查通过，Web HTTP 200。174 未部署；Codex runtime 不在范围内。

## 问题与修复

- 技能抽屉安装平台已有技能错误要求 `studio:catalog:write`，普通成员报权限错误。现按 `studio:write` 执行当前草稿编辑，保留归属/共享编辑权限、草稿 revision 与技能包 revision 校验。修改技能目录仍需管理员权限；没有给普通成员添加目录管理角色。
- Builder 对话安装技能的预览、代码比较、应用也有同一额外限制，已统一处理。
- 统一输入框不再通过自然语言关键词正则决定修改或试跑；将自然语言交给模型 `intent=auto`，由模型结合当前配置、会话和待应用建议返回 edit/run/rerun/ask/reply。显式 `/run`、`试跑智能体：` 指令仍可直达测试。
- 明确 Skill 没有 enabled 开关。长期停用对应移除当前草稿绑定，不能删除源技能或其他绑定；仅本次不用不能改变配置。
- 模型 JSON/schema 错误最多自动纠正一次，传入原始请求、schema 与格式错误位置。修复过程不保存草稿，失败后明确提示重试。权限、配置有效性与 revision 校验不会被该重试绕过。
- 模型明确撤回待应用变更时，空 edit 清除旧预览；普通解释/追问保留待应用预览。

## 验证

- 前端全量：142 文件，912 通过，1 跳过；生产 webpack 构建成功。
- 后端：两个 Studio/Builder 集成测试文件原有及格式纠正测试共 76 通过；新增普通 member 技能安装完整测试在补齐目录版本 fixture 后单独通过。RBAC 34 项通过，Builder 14 项通过。
- 新增权限测试覆盖：普通成员安装、卸载、模型普通与 SSE 预览、代码比较、应用；他人草稿拒绝访问；旧包版本/草稿版本拒绝；成员仍不能修改目录。
- 模型格式纠正测试覆盖未知 enabled/skills 字段、只重试一次、预览不修改草稿、应用只移除指定技能并保留其他字段。
- 173 服务端真实模型使用“资料研究助手”r15（技能 html-report-generation、archify）只生成四次预览。停用与删除两句都只返回 removeSkills=[archify]；否定删除和仅本次不用都没有配置变更。前后完整草稿相同，仍 r15。详见相邻 JSON。

## 浏览器验收（Zcode）

- 普通成员新建隔离 QA 草稿：session 返回 role=member；勾选 algorithmic-art 安装 HTTP 200，r1→r2，spec.skills 含该技能，刷新后仍勾选。
- 同一 member 草稿单次点击取消勾选，确认“卸载 Skill”，PUT HTTP 200，r2→r3，只有指定技能移除。无 console error/pageerror/失败请求。
- Owner QA 草稿模型链路：输入“这个algorithmic-art先别用了”，请求 intent=auto，最后 SSE result.action=edit、changes.removeSkills=[algorithmic-art]。显示预览时 r8 未变，应用后 r9、该技能移除。
- 技能已绑定时输入否定句“不要删除algorithmic-art，只告诉我怎么停用”：SSE action=reply、changes={}，r10 保持不变，无应用按钮。
- QA 脚本最初用 uncheck().catch(forceClick) 对受控 checkbox 重复点击，导致确认遮罩被第二次点击取消；这是自动化误判。改成单次点击后等待 alertdialog、确认卸载，成员测试通过。早期 owner 证据不能证明普通成员权限，最终以独立 member 证据为准。

紧凑证据：[skill-browser-58518b27.json](builder-173-20260924/skill-browser-58518b27.json)。完整脚本、截图和报告位于 Zcode QA 工作区 test-artifacts/173-core-regression。
