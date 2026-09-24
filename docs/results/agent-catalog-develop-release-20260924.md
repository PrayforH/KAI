# 智能体目录三栏与 develop 双环境发布

## 改动

- 宽屏智能体目录三栏，按目录实际可用宽度自适应到两栏 / 单栏；目录最大宽度 1440px。
- 网格行高统一，卡片内部占满行高，描述保留两行，版本和工作区操作固定在底部；消除不同文案长度造成的高度和按钮基线不齐。
- 将首字头像替换为研究、会议、数据、代码、写作、财务、流程、设计八类 SVG 图标，以名称和用途选取，未知用途使用稳定分配。颜色跟随亮暗主题，无外部图片请求。

## 合并与验证

- 样式提交 `9ac265c5`；合并提交 / 镜像源码 `8a71086cb87227a649e0f6c3bbdf9d63d3eb3e27`。
- `develop` 与 `auto/agent-evolution` 同步到合并结果并推送，保留 develop 独立的 DeepAgents 项目导出改动，以及 evolution 的延迟代码读取与测试。
- 前端 142 个测试文件：917 通过、1 跳过；Next.js webpack 生产构建通过。
- 后端 Studio、Runtime、Sandbox 与相关 API 回归：870 通过。
- 本轮未运行 Zcode、浏览器交互或真实模型测试；用户手动验收。

## Harbor 发布

两个环境使用同一批 linux/amd64 镜像，通过各自配置连接数据库、对象存储、模型和沙箱，不复制环境凭据。

版本标签：`develop-20260924-8a71086c`

发布后核对：173 与 174 的 API/Worker、Web 镜像摘要分别一致；上述服务全部健康，173:3302 与 174:3501 首页均返回 HTTP 200。

| 镜像 | 仓库 | Manifest digest |
|---|---|---|
| API / Worker | `harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api` | `sha256:537db5e46b8a3c793eef585579d4bd902a294db3270015c56206e3e011877469` |
| Web | `harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web` | `sha256:f850215a665d13f5bccd513b9125bd1e4e79b239547e089d06014ab4cef2720b` |

API 从 174 既有 amd64 基础镜像构建，依赖锁文件与新源码一致；完整替换 harness、migrations、agents、platform-skills。Web 使用生产构建产物和追踪出的 JS 依赖，保留 Linux 原生模块。第一次临时 Web 检查发现旧镜像仅含 Turbo 运行文件，缺少 webpack 对应 Next.js 文件；补齐同版本 JS 依赖后检查通过，失败镜像未切到正式 3501。

## 环境与恢复

- 174:3501：API、3 个 Worker、Web 更新为上述 Harbor 版本；数据库 `0035 → 0036 → 0037`，均为新增表。3501 新容器 `axis-web-harbor-8a71086c`。保留 3301 旧前端。
- 174 发布前完整数据库备份和部署配置备份：`/data/releases/catalog-174-8a71086c/backup/`，仅 root 可读，不入仓库。
- 174 原 Web `axis-web-20260922-d69cb0d2` 保留为停止状态；API/Worker 原镜像配置在备份的 `compose.deepagents-174.yaml` 中。迁移为新增表，代码回滚无需删除表或恢复数据库。
- 173:3302：使用同一 Harbor API/Worker/Web，保留原环境配置；数据库此前已为 0037。回滚配置在 `/data/agent-studio-evolution-20260920/backups/harbor-8a71086c/compose.json`。
- 切换前检查没有非终态运行，发布脚本健康检查失败会恢复原服务配置；不清理旧镜像或用户数据。
