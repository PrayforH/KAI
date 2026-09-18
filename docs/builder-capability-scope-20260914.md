# 构建助手的能力范围与迭代建议

核对日期：2026-09-14。依据当前 develop 代码的请求模型、服务端校验和前端入口。

## 结论

项目具备创建 Skills、编辑 Python 算子、管理 MCP 等功能，但当前智能体构建对话尚未将这些能力统一编排。左侧构建助手主要是“已有草稿的行为调整 + 试跑意图分流”，模型返回受限 JSON，服务器校验后由用户应用；它不是能任意修改项目文件的代码代理。

## 当前能力矩阵

| 对象 | 构建助手对话当前可做 | 已有的独立入口 / 能力 |
| --- | --- | --- |
| 名称与描述、System Prompt、任务契约 | 生成修改建议，应用到同一草稿；不直接发布 | 完整配置编辑器 |
| 已安装 Skill 正文 | 改已有 Skill 的 instructions、移除已有 Skill；不能创建或重命名、修改说明和附带文件 | Skills 编辑区支持正文与文件编辑、上传技能包 |
| 新 Skill | 当前对话协议没有新增 Skill 动作，服务端明确拒绝编辑不存在的 Skill | Skills → 对话创建可打开 Skill Creator；已有 Skill 的共创助手能生成完整 DraftSkill，包含 references / scripts / assets，应用后仍需保存 |
| 内置工具 | 只能缩减已有 builtinTools 清单，不能新增工具 | 配置 → 工具中选用平台支持的工具；新增底层内置工具实现仍需代码开发 |
| MCP | 只能移除已有绑定，不能新增连接、服务器或凭据 | MCP 管理及草稿能力装配；目录写入和凭据配置经过独立权限检查 |
| 知识库 | 只能缩减已有 knowledgeReferences | 知识库页面创建 / 导入资料，配置编辑区关联已有知识库 |
| Python 算子 | 不能新增、编辑或删除；脚本正文不进入构建模型上下文 | 自定义算子可维护代码、描述和输入 Schema，保存后通过 Sandbox 试跑 |
| 子智能体 | 只能修改已绑定角色的职责，不能新增绑定或修改其版本 | 主编辑区管理协作角色与固定版本绑定 |
| 模型、运行环境、策略、凭据、发布部署 | 构建对话不能改动这些字段 | 各自配置入口和明确的发布 / 部署流程 |
| 业务任务与工具调用 | 自动模式判断 edit / run / rerun / ask / reply；试跑交给正常运行链执行 | 执行时仍受该智能体版本及用户配置的工具和联网能力约束 |
| DeepAgents 项目文件 | 只读查看、导出、比较本次修改；不能编辑 Python 后反向更新智能体 | 导出后可在外部代码工程中开发，平台暂未提供源码回写 |

“模型能生成一个 SKILL.md 文本”与“把技能正确安装到该智能体草稿”是两个动作。运行时写出的脚本或文件也不会自动成为平台 Skill、Python 算子或 MCP 资源。

## 建议的演进顺序

1. **优先统一 Skill 创建入口。** 构建对话识别 createSkill / updateSkill，复用现有 Skill 共创和安装服务，明确目标是 Agent、个人还是平台。生成完整技能文件后，在本次代码差异中审阅，再应用到草稿。
2. **支持从已有可见目录装配能力。** 内置工具、MCP、知识库先限定为用户已有权限的条目，展示新增绑定与移除绑定，应用时再次校验权限和资源版本。单纯放开当前 JSON 字段校验无法完成这条链路。
3. **补齐 Python 算子开发闭环。** 生成代码和输入 Schema，执行代表性 Sandbox 测试，展示结果与文件差异后应用。MCP 的新建服务与部署、外部凭据输入仍应使用独立入口。
4. **统一多资源变更计划和历史。** 同一次构建可能同时改 Prompt、Skill、工具绑定和子智能体，需明确资源依赖、版本校验、失败回滚及审阅范围。若要像 Git 一样跨刷新比较任意历史修订，需增加持久化草稿修订快照；现有 agent_drafts 只保存当前修订。

本轮只实现主题跟随、大文件预览和本次修改的代码差异，不扩大构建助手的管理权限。

## 本轮差异的范围

代码差异使用同一 DeepAgents 导出器分别生成修改前 / 修改后文件，支持新增、删除、修改及未改动行折叠。预览接口不写草稿；应用仍调用原有 revision CAS 更新。差异保留在当前构建工作台会话中，显示最近一次应用的 rN → rN+1，不等于持久化的全部草稿版本历史。发布版本与草稿修订也是两个不同概念。

## 代码依据

- `src/harness/studio/builder_conversation.py`：BuilderChanges、apply_builder_changes、构建提示词和意图枚举。
- `src/harness/studio/service.py`：converse_builder、apply_builder_edit、compare_builder_project，过滤脚本上下文、候选校验和修订检查。
- `src/harness/studio/skill_builder.py`：完整 DraftSkill 生成，包括附带文件。
- `web/harness-console/src/components/agent-studio/agent-studio-workbench.tsx`：Skill Creator、Skill 共创、Python 算子与能力装配入口。
- `web/harness-console/src/lib/skill-creator-launch.ts`：个人 / 平台 / Agent Skill 作用域。
- `src/harness/studio/api.py`：studio:write、studio:catalog:write、preview / publish / deploy 分离。
- `src/harness/storage/studio_repository.py`：当前草稿修订的持久化与原子更新。

## 2026-09-14 后续实现：Skill Creator 与目录装配

上述“当前能力”矩阵记录的是 f6225d5 时点；本节更新其后续实现状态。

- 构建对话支持 `skillRequests`（create / update），服务端复用 `ControlPlaneSkillConversationService`，使用草稿配置的模型路由生成完整 Skill。返回的 `createSkills` / `updateSkills` 与 Prompt、工具绑定等合并为同一建议。
- 新技能只安装到当前 Agent 草稿，支持 instructions、description 和 references / scripts / assets。沿用 DraftSkill 文件校验、上传服务的凭据文件检查及原草稿 replacement/CAS 流程；保留已有托管来源，修改后标记为已修改。不会自动运行生成的脚本。
- 工具和 MCP 可从当前用户可见目录新增或移除。知识库引用继续通过主编辑区装配，避免将 MCP 标识误当作知识库 ID。模型上下文只含装配所需的说明、标识和风险，不含连接凭据或自定义请求头。新增绑定携带目录修订；生成、差异预览、应用时均检查当前可见性和修订，编译器继续检查执行环境、策略和模型兼容性。
- 新建 MCP 服务器、配置密钥、发布个人 / 平台 Skill、创建底层工具和 Python 算子仍走独立管理入口。装配引用复用运行时已有凭据解析与 preflight，不赋予模型目录管理权限。
- 同一建议先生成实际 DeepAgents 项目差异，再应用整个草稿；失败不会部分安装 Skill。差异也纳入原 replacement 的技能文件合并、关联评测清理和已发布草稿自动升版规则。

示例：在构建助手输入“创建 source-review 技能，附来源记录模板，并添加 WebFetch 和已有的某个 MCP”。助手生成建议后，使用“查看代码差异”审阅文件，再“应用修改”；效果测试继续走已有运行链。


## 2026-09-15：真实 Skill Creator 与模型初稿

上一节的 ControlPlaneSkillConversationService 是模型共创服务，并没有加载 skill-creator；此前将它称作已接入 Skill Creator 不准确。

现在构建助手的创建/更新请求通过 WorkerSkillCreator 注册独立预览快照、创建会话和运行，执行平台内固定上游版本的完整 `platform-skills/skill-creator` 包。沿用现有 Worker、权限、沙箱和产物服务。成功建议必须有技能加载、官方打包工具成功调用、真实 `.skill` 产物及至少两条测试用例。测试用例纳入差异；业务评测和基线比较尚未自动执行，不生成虚构评测成绩。

初始 Agent 先根据目录选择兼容运行配置，再实际调用所选模型生成名称、描述、System Prompt 和任务契约。模型从当前可见且兼容的 Skill 目录推荐最多五项并给出理由。前端不再附加禁止联网条件，也不静默过滤 MCP。所选推荐包沿用目录权限和固定版本校验，通过同一套 DeepAgents 差异审阅和 CAS 应用；初稿不会自动安装推荐技能。

代码入口：`initial_agent.py`、`worker_skill_creator.py`、`AgentStudioService.create_from_task/converse_builder`、构建助手推荐 Skill 面板。


## 2026-09-15：统一技能创建入口

技能目录中原有三项来自：前端写死的 Skill Creator 快捷条目、Anthropic 原版 `skill-creator`、OpenAI 离线适配的 `skill-authoring-quality`。现移除前端伪目录条目，停用后者，保留“Skill Creator（Claude 官方）”。创建快捷操作放回真实包的详情中。

活跃平台目录、模型推荐与默认 Lead Agent 均只装配原版；目录迁移保留模型、MCP 及技能启停等租户设置。旧适配版禁止新安装，历史包查询和已发布不可变快照保留。174 核对时没有草稿引用旧适配版。默认 Lead 的简化占位文件一并移除，由平台包装配流程提供完整原版并自动派生新的平台版本。
