# 知识库与控制台修复合入 develop · 174 发布记录

日期：2026-09-14。目标：`172.20.109.174:3501`（Web）、`:8800`（API）。

## 提交范围

- `44388c5`：补提交此前已部署到 173、但未进入 Git 的知识库新建向导配置、RAG 引用文档抽屉与内联引用样式，共 11 个文件。
- `efb6eb0`：将 `feature/weknora-knowledge-base` 合入 `develop`，保留 develop 的安全依赖升级；解决前端锁文件和两份测试的冲突，同步 Markdown 插件与数据库迁移版本的测试断言。
- 知识库覆盖 WeKnora 网关、RAG/Wiki/混合库、文档与切片、Wiki 页面、2D/3D 图谱、成员权限、会话知识选择和答案引用。样式包括浅色任务栏、MCP 表单，以及引用与知识库配置表单。
- 原功能分支中已提交的平台 Skill 目录和运行修复随分支合入；本次按用户要求排除 DeepAgents 导出，未提交的 Tavily 下线与模型配置改动留在原工作区。
- 原工作区 `/Users/xiaokai/Documents/agent studio` 保留其余修改；集成工作区为 `/Users/xiaokai/Documents/agent-studio-kb-174`。

## 验证

- Python 全量：1377 passed、4 skipped；涵盖本地 PostgreSQL、Redis、MinIO 集成测试。
- Web：89 个测试文件，590 passed、1 skipped；本地生产构建通过。
- Ruff 通过。Pyright 为 266 个既有类型错误，满足仓库 284 上限；不是零类型错误。
- 9 个 Agent 包检查与确定性打包通过。
- 合并的一方保留了上游 Skill 资产中的空白格式；应用代码的 diff whitespace 检查通过。
- 174 预发布 API：健康、能力目录、Skill 目录、知识库列表均 200；真实 WeKnora RAG/混合库创建、配置提交、文档代理及跨用户隔离通过。临时知识库已删除。
- OpenAPI 不含 `deepagents-project` 路由。
- 未发起真实模型任务，也未复制 173 的用户知识库数据到 174。

## 构建与环境

应用源码来自 `git archive efb6eb0f11ab8cd0e9e97e012e88f1c2fb9a9061`，镜像在 174 原生 amd64 构建。

- API：以 174 当前 `kai/axis-api:20260908-recovery-webconfig2` 为依赖基线，复制提交后的代码、迁移、Agent 资产及平台 Skills；补齐锁定的 xlrd 2.0.2 和已校验的 Node/office 库。继承 SDK 0.2.152。
- Web：保留 develop 的 Next 16.3.3 / sharp 0.35.4；依赖按同一锁文件在本机预装 Linux x64 包，174 使用该依赖包执行 Next 编译与 TypeScript 检查。Alpine libcrypto3/libssl3 更新到 3.5.8-r0。
- 174 原本未接入 WeKnora；新增与 173 相同的既有 WeKnora 服务连接配置，指向 `172.20.109.174:8180`，平台各环境的知识库目录和用户数据仍独立。
- 凭据、环境文件与 Docker inspect 备份仅保存于服务器保护目录，未进入 Git。Compose 配置对比确认现有 API/Worker/quality-sync 环境值保持一致，只新增知识库连接配置。
- 数据库从 0031 升到 0032，仅新增知识库成员表；升级前已保存 88 MB 的 PostgreSQL 自定义格式备份。

发布目录：`/data/kai-kb-20260914-1512`。该目录保存源码包、构建配方、依赖校验和、构建日志、旧容器配置、数据库备份和完整 Compose 覆盖链。

## 回退方式

- Web：`sh /data/kai-kb-20260914-1512/rollback-web.sh`，停止本轮新 Web 并启动保留的 `axis-web-20260908-recovery-webconfig3`。
- API/Worker/quality-sync：`sh /data/kai-kb-20260914-1512/rollback-api.sh`，复用发布前完整 Compose 链。
- 0032 是兼容性新增表；应用回退时保留该表。不要用数据库备份覆盖发布后产生的数据。
- 原 3301 Web 与数据库/Redis/MinIO 容器不重建；既有模型配置和已发布智能体版本不修改。

## 最终发布结果

- 状态：已发布，`develop` 已推送远端。
- API / 3 个 Worker / quality-sync：`kai/axis-api:knowledge-develop-20260914-efb6eb0`。
- Web：`kai/axis-web:knowledge-develop-20260914-efb6eb0`，容器 `axis-web-knowledge-20260914-efb6eb0`，正式入口 `http://172.20.109.174:3501`。
- API image ID：`sha256:1fab7c4e57735a651b849c4344b30ad9af33c487fc54449db53b98f94f34091a`。
- Web image ID：`sha256:5eecc79e11dd94269b18601ea1ab987a0716f93070deb1b811c102f076a7876f`。
- 六个应用容器均 healthy、restart=0，source revision 标签均为 `efb6eb0f11ab8cd0e9e97e012e88f1c2fb9a9061`。
- 正式 Web 首页、登录页、图标、认证配置、runtime-config、知识库页面均 HTTP 200。正式 API 再次通过创建/删除临时 RAG 与混合库、文档代理、访问隔离以及目录访问验收。
- Web 编译产物确认含 `wikiContentInstructions`、`Wiki 设置`、`分块设置`、`引用文档详情`，且不含 DeepAgents 导出入口。
- 发布前活跃 Run 为 0；原 3501 Web 已停止并保留供回退，预发布容器在验收后清理。
- 浏览器自动化连接超时，因此本轮没有完成登录态的人工交互式视觉验收；前端验证依据为自动化测试、生产编译、镜像内容检查和线上 HTTP 探测。
