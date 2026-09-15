# 174 CubeSandbox、构建流式流程与代码视图合并发布

## 发布结果

2026-09-15，174 的 Web、API、3 个 Worker、quality-sync 统一切换至 `cube-hitl-codeview-20260915-150755`，CubeSandbox 保持启用。访问地址：http://172.20.109.174:3501 。

| 项目 | 值 |
| --- | --- |
| 合并基线 | develop `b167ee7` |
| 集成分支 | `auto/cube-hitl-code-view-174` |
| API 镜像 | `kai/axis-api:cube-hitl-codeview-20260915-150755` |
| API image ID | `sha256:7c063da3f496b343b11561c613d18313c46b6e4ea8b1a6b8605aaf7d46c82c52` |
| Web 镜像 | `kai/axis-web:cube-hitl-codeview-20260915-150755` |
| Web image ID | `sha256:e273f32d70d92263df7919d54da69e6ce6778539b21193fe7dbe2aac37377281` |
| 174 发布目录 | `/data/cube-hitl-codeview-20260915-150755` |
| Provider / 模式 | `cubesandbox` / `worker_cli_deferred` |

本次不需要新增数据库迁移。切换前等待正在运行的任务结束，未中断该任务。原环境其他项目和 173 环境未改动。

## 为什么之前的代码视图被覆盖

“分析 173 环境知识库合并”任务在独立的 develop 工作区持续推进了代码视图、项目差异、模型生成初稿和官方 Skill Creator；“优化人机协同与流式流程”任务基于另一个较旧的 feature 工作区制作发布覆盖层。因此后一次部署带回了较旧的前端与部分 Studio API / Service，覆盖了 develop 的能力。

此次以 develop 最新提交为基线，合入人机协同、流式和 CubeSandbox 修改，逐处处理冲突；API 使用完整合并源码，Web 从同一工作区构建。原 feature 工作区的无关未提交修改未带入。

## 合并后的能力

- 保留代码视图：项目文件树、筛选、语法高亮、行号、自动换行、文件切换、复制、刷新、项目下载、主题跟随与大文件虚拟化。
- 保留修改前后的项目代码差异，待确认提案与已应用修改均可查看；应用时验证草稿 revision，过期提案返回 409。
- 保留模型真实生成初稿、能力推荐、官方完整 Skill Creator 包及目录安装流程。
- 合入构建助手回复流、进度提示、评测事件流、断线恢复和人机审批状态修复。
- Studio 新草稿默认选择已启用的 Cube 执行配置；文件与命令在 Cube 中执行，产物回传。
- 修复延迟分配模式在运行开始时缺失远端工作区的问题。现在可以用沙箱工作区内的绝对路径调用 Write/Read；相邻目录、`..` 和符号链接逃逸仍会被拒绝。

代码视图仍是生成项目的只读预览。单文件 10 MB、项目累计 50 MB 的边界沿用 develop 实现。

## 构建与验证

174 访问 npm 镜像和官方源失败后，使用已有发布保存的 Linux 依赖离线构建。依赖包对应的 package-lock 与本次完全一致：`1e2a639775e197236eb69272b6ed4fe4ecf679de2f4b5bec7d5d1df89cc4419d`。174 已构建成功，因此未启用用户授权的“本地 linux/amd64 构建 → Harbor → 174 拉取”备用路线。

### 自动检查

- 合并核心后端回归：403 passed。
- 后续绝对路径修复相关回归：121 passed；补充 JSON/SSE Skill Creator 与目录原子应用覆盖。
- 前端：601 passed、1 skipped；本地及 174 Next.js 生产构建通过。
- Python 类型检查 0 errors；相关 Ruff 和 git diff 检查通过。

### 应用与界面实测

- SSE 真实模型生成新草稿，`generatedByModel=true`，默认 `cubesandbox-private`。
- 1.7 MB、25,000 行中文文件完整返回，未截断。
- 构建 SSE 回复、修改前后代码差异、确认应用、ZIP 导出内容摘要一致；重复旧 revision 应用被拒绝。
- 实际浏览器确认文件树、文件选择切换和代码预览可用。
- 正式运行 `run_107ab8c213fa4f64820308252fc522ff`：Python 计算 6×7；Write 和 Read 均使用 Cube 内完整绝对路径成功，发布 `combined.txt`。下载内容为 `42\n合并验证通过`，SHA-256 与产物元数据一致。
- 官方 Skill Creator 正式运行 `run_1397185e9d9a410f9b5e1ab5c9e712f5` 用时约 133 秒，加载固定上游版本、真实校验和打包，返回 `number-check.skill`、`evals.json` 及 SKILL.md；下载验证包结构与至少 2 条评测用例通过，原草稿 revision 未变。评测用例仅设计，未执行业务评测。
- 该运行完整回放 58 条事件；使用 `Last-Event-ID` 恢复的后 28 条事件与原序列一致，最终事件为 `run.succeeded`。

Cube 协议层、CLI 双向初始化及会话续跑证据见 [Cube 初次接入记录](cubesandbox-174-integration-20260915.md)。

## 回滚与后续发布

174 发布目录保存上一版 compose、私有配置和 `rollback-combined.sh`。等活动任务结束后，在 174 执行该脚本可恢复此次合并前的 Cube API / Worker 和 HITL Web；Cube 配置和数据保持不变。此次未实际执行回滚。

后续统一从 develop 的明确提交构建 Web 和 API，记录源码提交、两份镜像摘要与发布清单，避免多个工作区互相覆盖。Cube 模板依赖固化、资源和网络策略映射等后续事项见 [选型与演进分析](sandbox-selection-and-evolution-20260915.md) 和初次接入记录。
