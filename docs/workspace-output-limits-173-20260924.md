# 173 连续运行失败：历史产物误用本轮额度

## 根因

最近三次失败均为 `runtime_error`，Worker 记录 `ValueError: runtime artifact exceeds the output size limit`。堆栈位于模型启动前的 `_workspace_output_fingerprints`。更早的 PDF 识别任务也在产物整理阶段遇到相同错误。

从失败会话的实际工作区快照恢复得到 71 个输出文件，总计 79,406,641 bytes，大于配置的本轮自动发布额度 52,428,800 bytes。基线扫描把全部历史文件也计入这个额度，导致后续对话在进入模型之前失败。它不是本次思考 UI 更新引起的显示错误。

## 修复

- 历史输出基线使用流式 SHA-256，不加载整个文件到内存，也不消耗本轮发布额度或 100 个文件的发布数量额度。
- 发布时先排除已经发布及内容未变的文件，再对本轮新增/变化文件计数。路径越界、非普通文件及单次有界读取检查保留。
- 最终回答明确引用的文档优先于自动收集的图片等其他输出。
- 自动发布超额时跳过超额文件，记录 `artifact.publication_limited` 并在过程标题附近展示提示；不删除工作区原文件，不把模型已完成的运行转为失败。
- 既有失败运行保留历史状态，不修改成成功。

图片压缩是后续可独立优化的识别准备策略：保留原 PDF，识别用页图适度缩放、按需压缩，小字或识别失败时回读高清页。本修复没有压缩、覆盖或删除用户原图。

## 验证

- 后端编排、AG-UI 和产物工具测试 89 项通过；新增覆盖历史 120 个文件超过数量和容量限制后仍可继续、容量/数量超额只提示、最终文档优先发布。
- 前端相关 23 项测试、Next.js production build 和 TypeScript 检查通过；Ruff 通过。
- 后端 Pyright 检出 4 个已有错误；在未修改的 `92f282b9` 复查得到相同的 4 个错误（activity 流式 metadata 类型及 orchestrator payload 重声明），本次未新增类型错误。
- 在更新后的 173 API 中只读恢复真实快照至临时目录，71 文件 / 79,406,641 bytes 的基线计算通过，耗时 0.351s；未修改文件的发布检查通过，未重跑用户识别或写入原会话产物。

## 发布

代码 `a5f86edb`，分支 `auto/agent-evolution`。API/Worker/Web 均更新为 `evolution-builder-a5f86edb`，健康检查通过，Web HTTP 200。

首次重建遇到 API 基础镜像 127 层导致 `max depth exceeded`，旧容器保持健康。将只含镜像内容的临时容器文件系统导出为新基础层，保留原镜像 Env、Entrypoint、Cmd、User、WorkingDir、Healthcheck、ExposedPorts、Volumes，逐项比较一致；新镜像为 3 层。没有导出运行容器的数据卷或用户工作区。

发布目录 `/data/agent-studio-evolution-20260920/builder-output-a5f86edb`。归档已更新为使用扁平化基础镜像的 Dockerfile，基础镜像 `kai/axis-api:evolution-flat-rootfs-7a472fa9` 保留。回退配置及版本号见 `backups/builder-output-a5f86edb/`，上一 API/Worker 为 `7a472fa9`、Web 为 `5b197683`。
