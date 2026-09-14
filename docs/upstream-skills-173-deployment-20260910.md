# 上游开源 Skill 引入 · 173 环境部署与实测记录

- 日期：2026-09-10
- 分支：`feature/weknora-knowledge-base`
- 目标环境：`172.20.109.173`（API `:8800`，Web `:3301`）
- 发布 tag：`upstream-skills-20260910-r2`
- 默认会话版本：`lead-agent@1.0.2+platform.c95a6965`（`HARNESS_AGENT_VERSION`）

## 1. 背景与许可证结论

用上游开源 Skill 替换自研办公 Skill。逐个目录核实上游 LICENSE：

| 来源 | 许可 | 处置 |
| --- | --- | --- |
| anthropics/skills 的 `docx` / `pdf` / `pptx` / `xlsx` | © Anthropic PBC，Additional Restrictions 明示禁止复制、在 Services 外保留副本、创建衍生作品、向第三方分发 | **排除** |
| anthropics/skills 的 `doc-coauthoring` | 无任何许可证文件（仓库根目录亦无） | **排除**（默认保留所有权利） |
| anthropics/skills 其余 14 个 | Apache-2.0（逐个核实） | 引入 9 个 |
| MiniMax-AI/skills 办公四件套 + pptx 设计配套 | MIT | 引入 8 个 |

办公能力改由 MIT 的 MiniMax 包承担；通用能力取 Anthropic 的 Apache-2.0 包。

## 2. 引入清单（17 个，vendored 于 `platform-skills/`）

- **MiniMax（MIT，@`60aaae5`）**：`minimax-docx`、`minimax-xlsx`、`minimax-pdf`、`pptx-generator`、`color-font-skill`、`design-style-skill`、`ppt-editing-skill`、`slide-making-skill`
- **Anthropic（Apache-2.0，@`41bbe19`）**：`skill-creator`、`mcp-builder`、`internal-comms`、`theme-factory`、`frontend-design`、`web-artifacts-builder`、`webapp-testing`、`canvas-design`、`algorithmic-art`

**排除的 5 个 Anthropic 通用包**及理由：`brand-guidelines`、`claude-api`（与平台「不得自称 Claude/Anthropic」硬规则冲突）、`academy-guide`（导流 academy.claude.com）、`slack-gif-creator`（无 Slack 场景）、`discernment-nudge`（每次回答强插追问，企业场景偏侵入）。

平台目录规模：22 个包（17 vendored + 5 自研），自研办公四件套（`office-docx/xlsx/pptx/pdf`）已移除。

## 3. 实现要点

- **加载器** `src/harness/studio/vendor_skills.py`：按目录解析上游 `SKILL.md`，`description` 归一化到模型 500 字符预算（上游部分包用 YAML 折叠标量且超长），文件按文本/二进制分别载荷，**保留上游 LICENSE 原文**；带进程级缓存（否则每次读目录都要重算 8.5 MB 资产哈希：首次 84 ms → 之后 0.14 ms）。
- **归属与风控**：每个 vendored 包带 pinned revision、上游 URL、许可证；含可执行文件的包在目录里标记 `riskLevel=review`（其余 `low`）。
- **默认绑定收敛**：`canvas-design` 标记为**仅目录、不默认绑定**（`CATALOG_ONLY_SKILLS`）——其 5 MB 字体库会把默认 agent 快照从 3.6 MB 抬到 10.6 MB，并在每次新建会话/运行时复制进工作区。需要时按需绑定。
- **平台模型修复**：`SkillFileSnapshot.content_base64` 原要求最少 1 字符，导致含 0 字节包标记（`skill-creator/scripts/__init__.py`）的 vendored 包整体构建失败；空内容现在可正常往返。
- **运行时补齐**：worker 镜像新增 **Node 22.9.0** 与全局 `docx`、`pptxgenjs`。构建主机无外网，故 node 与 npm 包在本地预置为构建上下文产物（`node-runtime/`），镜像内安装到 **`/node_modules`**——沙箱执行会重建进程环境（仅保留 HOME/PATH/TMPDIR），依赖 `NODE_PATH` 不可靠，根目录安装可被任意工作区脚本按目录向上查找解析。
- **未安装**：LibreOffice 与 pandoc（转换/重算类能力）。相应技能在缺失时走诚实降级，不静默失败。

## 4. 173 实测

部署后重启 api/worker/quality-sync（web 仅改版本钉）。6 容器 healthy，`/healthz` 200。

**定向验证**

| 项 | 结果 |
| --- | --- |
| 容器内 node | `v22.9.0` |
| npm 库解析 | 任意目录 `require('docx')` / `require('pptxgenjs')` 成功 |
| vendored 树 | `/app/platform-skills` 18 项（17 技能 + README） |
| 目录接口 | 22 个包，含 MIT/Apache 归属与 riskLevel |
| 默认版本 | `1.0.2+platform.c95a6965`，22 技能、快照 3.6 MB |
| UI 新建会话 | 绑定新版本（BFF 版本钉已更新） |

**端到端实测（浏览器 + 真实账户）**

用户 `xiaokai@shdata.com` 新建任务 → 选择「通用 Lead Agent」→ 上传《KAI WORKBENCH 产品使用手册.pdf》→ 提示「根据附件创建 ppt」。

模型行为（run `run_7de5cc987d604c788aa4ddbde5bff921`，约 13 分钟，`succeeded`）：

1. `Read` 附件解析文本；**`Skill(skill="pptx-generator")`** 加载 MiniMax 技能
2. `Bash: node -v; npm -v; ls /node_modules/pptxgenjs` 确认环境（Node 22 + PptxGenJS 4.0.1）
3. 依据技能参考文档确定设计系统，编写 `build.js`（PptxGenJS）
4. 生成后执行文本 QA：
   **发现并修正结构缺陷**——「4 个章节分隔页被连续放在目录之后（第 3-6 页），而不是穿插在各自章节前」
5. 尝试 `which soffice libreoffice` 做视觉 QA → 未安装，降级并说明

交付物：`KAI-WORKBENCH-产品使用手册.pptx`，**493,820 字节 / 22 页 / 16:9**，本地 python-pptx 可解析，结构为封面 → 目录 → 章节分隔 → 正文 → FAQ → 结尾页；配色为技能设计系统给出的「Platinum White Gold」（白底、蓝色主操作色、金色强调、圆角风格、页码徽章）。

**与自研技能产出对比**：上一轮自研 `office-pptx` 产出 17 页 / 66 KB / 无设计系统 / 无结构自检；本轮 22 页 / 493 KB / 应用设计系统 / 自主发现并修复结构缺陷 / 给出 OOXML 校验结论。

## 5. 已知边界

- LibreOffice / pandoc 未安装，`minimax-docx`、`minimax-pdf` 的转换与渲染色检路径不可用；如需要，镜像将增加约 700 MB。
- worker 镜像由 1.36 GB 增至约 1.5 GB（Node + 两个 npm 包）。
- 附件上传步骤经 API 完成（内置浏览器不支持驱动原生文件选择器），其余流程均为真实 UI 操作。
- `canvas-design` 需在智能体上显式绑定后使用。
