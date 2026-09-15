# 通用 Agent：聊天、用户决策与文件交付的边界

## 故障证据

用户原始问题：“给我三种客户投诉处理方案，分别说明成本和影响。先让我选一个，再写详细执行步骤。”

174 上的 `run_a1e1d7136f7d4b6f8727c2172f83a1c0` 使用 Lead Agent 1.0.2，加载通用编排 Skill，随后列目录、创建 outputs、写 Markdown、运行文件检查并调用 publish_artifact。文件交付是模型实际调用工具的结果，并非前端把文字自动转成文件。

根因在共享运行时 `VISIBLE_EXECUTION_CONTRACT`：它要求所有最终交付都必须存在为文件，却没有区分聊天答复、决策阶段和文件任务。该约束会覆盖所有接入它的 Claude/Codex Agent，不能仅靠修改某个智能体名称或界面解决。

## 修复

1. 共享运行规则改为：问答、比较、解释、供审阅内容和等待用户输入，默认在聊天里呈现。Markdown 是排版格式，不意味着必须生成 `.md` 文件。
2. 用户保留选择权或要求确认后再做时，本轮只输出选项/问题，并停在该阶段；收到回复后才进行依赖该选择的工作。
3. 文件、导出、下载和确需修改文件的任务仍可以执行，保留真实创建、校验和发布要求，不禁用文件工具或抑制已经产生的真实交付物。
4. 通用 Lead Agent 升级至 1.0.3，收窄通用编排 Skill 的使用范围，普通比较不再要求先检查工作区。默认发布版本带平台 Skill 摘要后缀。
5. 已发布旧版本和历史消息不修改；共享运行规则对后续启动的旧版运行同样生效。添加投诉方案选择、Markdown 表格的无文件行为评测样例。

## 开源参考及采用范围

### DSH / DeepSeek Harness

参考提交 `0d1f50007f9bca3f52b06e1c3074fa14d5fb0720`。

- [文件交付组件](https://github.com/deepseek-ai/deepseek-harness/blob/0d1f50007f9bca3f52b06e1c3074fa14d5fb0720/packages/client/ui-deliverables/README.md) 区分文件修改记录、显式 present 交付和聊天正文；本项目采用这种职责区分，继续保留现有 artifact API。
- [ask_user_question](https://github.com/deepseek-ai/deepseek-harness/blob/0d1f50007f9bca3f52b06e1c3074fa14d5fb0720/packages/interaction/tool-ask-user/README.md) 通过独立交互服务提交问题并等待答案，支持选项及自定义输入。该机制值得作为后续结构化提问的实现参考；本次没有接入该工具，仍通过聊天回复承接用户选择。
- [Standard preset](https://github.com/deepseek-ai/deepseek-harness/blob/0d1f50007f9bca3f52b06e1c3074fa14d5fb0720/packages/preset/agent-presets/presets/standard/agent.cordis.yml) 将计划模式和实施阶段分开。这里借鉴阶段边界，不照搬其编程工作流和插件体系。

### Pi

[系统提示组装](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/system-prompt.ts) 根据实际可用工具生成相关指南，并将简洁答复作为通用规则。Pi 本身定位为编程助手，不直接当作本项目面向业务用户的通用 Agent 模板；参考的是按能力提供指导、减少无关流程的方式。

## 验证与发布

- 54 项相关测试通过，覆盖 Agent 默认发布、Claude/Codex 运行组装、文件产物等；Ruff 通过。
- API、3 个 Worker 和 quality-sync 镜像：`kai/axis-api:general-agent-contract-20260915-1915`。
- 镜像摘要：`sha256:b12f802d6151cd755cf3de294112cb817a82bfa184e72ed329b8c6457fc2cbf0`。
- 174 发布目录：`/data/general-agent-contract-20260915-1915`。Web 沿用之前的会话修复版本。切换前无活动运行。
- 此次没有接入新的选择卡片、表单或强制业务审批；用户阶段边界由模型指令约束，不能宣称已成为服务端审批状态机。

### 174 主会话实测

使用独立测试账户，经主会话 API 创建会话、发起运行并读取最终消息和附件；不是构建助手效果测试接口。

| 场景 | 运行 ID | 结果 |
| --- | --- | --- |
| 旧版 1.0.2：用户原始投诉方案问题 | `run_88d76c5e9edd4f90a97cc7b34af3d302` | 聊天中给三种方案并等待选择；0 工具调用、0 附件 |
| 新版 1.0.3：相同问题 | `run_41d7903eab2146a686b718590a622449` | 聊天中比较成本和影响并等待选择；0 工具调用、0 附件 |
| 同一会话选择第二种并要求详细步骤 | `run_e08bee290eff4d60ab5b56210799c81c` | 接续上下文展开方案 B；0 工具调用、0 附件 |
| 再要求导出可下载 Markdown 文件 | `run_a81d83bb13914af794c5806be334e02b` | 调用 Write、Read、publish_artifact，生成 1 个 `complaint-plan.md`；6146 字节，下载内容 SHA-256 与附件记录一致 |
| 新会话要求用 Markdown 表格比较培训方式 | `run_373a864ab1944839b0f17f21c34e8bc6` | 表格直接出现在聊天正文；0 工具调用、0 附件 |

五项均成功完成。发布后 API、3 个 Worker 和 quality-sync 均 healthy，重启次数为 0。

回滚在无活动运行时，用发布目录的 `compose.previous.private.json` 重建 api、worker（3 副本）和 quality-sync；不会删除已生成的默认 Agent 发布版本。
