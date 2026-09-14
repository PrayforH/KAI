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
