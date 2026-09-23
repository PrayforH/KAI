# 173 Builder 配置抽屉与代码视图调整

## 布局与交互

- 中间栏用「配置 / 代码」切换配置卡片和项目文件树。
- 代码目录加载后保留右侧对话；点击文件或展开按钮才显示右侧源码。收起源码恢复同一份对话和未发送输入。
- 内置工具、MCP、知识库、技能、Python 算子分别打开对应配置抽屉，避免混合展示和重复展开。
- 工具抽屉按「公开联网」「文件与命令」分组，统一行高和勾选框，移除账号联网状态及设置跳转说明。
- 技能上传、对话创建、搜索和平台技能勾选直接可见；描述最多显示两行，悬停可查看完整文本。
- Python 的 Schema 和源码编辑器在窄抽屉内上下排列。
- MCP 注册通过独立 Portal 显示抽屉，保留背景工作区；引用标识自动生成，能力说明和治理选项收进高级设置，检测错误直接在抽屉中显示。保存后刷新可用 MCP 列表，但不自动绑定。
- 配置卡片的图标、标题、数量、描述和箭头采用统一网格，数量简化为「0 个 / 0 项」。
- 顶部 Header、配置切换栏、对话栏和代码工具栏统一高度；目录与源码的上下分隔线对齐。
- 对话右上角使用统一线宽的文件和对话图标，保留可访问标签和当前状态。
- 文件列表复用代码视图交互：中间显示文件目录，点击文件或展开按钮在右侧预览；收起后恢复对话和未发送文字。

参考 [Agenta 的 AgentIntegrationDrawer](https://github.com/Agenta-AI/agenta/blob/main/web/packages/agenta-entity-ui/src/DrillInView/SchemaControls/agentTemplate/AgentIntegrationDrawer.tsx) 的分类资源选择方式。沿用项目现有主题、组件和保存逻辑。

## 发布

- 分支：`auto/agent-evolution`；代码提交：`bdeae5db2727e9bb7da35c940b399854e9a237e8`。
- Web 镜像：`kai/axis-web:evolution-builder-bdeae5db`。
- 目标：`172.20.109.173:3302`，项目 `agent-evolution-173`，目录 `/data/agent-studio-evolution-20260920`。
- API / Worker 保留 `kai/axis-api:evolution-d6bada40`；每次发布前后比较容器 ID，确认没有重建。develop / 174 不变。
- compose 和 Web revision 备份在 `backups/builder-layout-bdeae5db/`。

本地使用 `next build --webpack` 生成 standalone 产物，服务器只构建 COPY 层。旧基础镜像仅包含 Turbopack 的裁剪运行文件，第一次部署出现缺少 `app-page.runtime.prod.js` 的 500，健康检查后自动回滚。随后补齐同版本 Next 16.3.3 的纯 JavaScript 包，保留 Linux 原生依赖；修正后的镜像先在无网络临时容器检查 `/login`、Builder 路由返回 200，再切换 3302。后续镜像基于这份完整运行时构建。

## 验证

- 全量前端测试：131 个文件，828 passed / 1 skipped。
- 生产构建包含 TypeScript 检查，通过；`git diff --check` 通过。
- HTTP 兼容修复后，MCP 相关 2 个测试文件、16 项测试再次通过，生产构建再次通过。回归环境只提供 `crypto.getRandomValues`，不提供仅安全上下文可用的 `randomUUID`。
- 新增 MCP 表单测试覆盖错误展示、自动元数据、保留治理默认值和注册成功回调；文件预览测试覆盖默认收起、点击展开、再次收起和深色主题。
- 回归测试覆盖中间目录 / 右侧源码拆分、收起后保留选中文件、仅加载一次源码，并模拟文件树初始化时的 selection 回调，防止加载自动展开。
- 已在 Chrome 的 173 页面检查工具、技能、知识库和 MCP 抽屉；工具只展示本组内容，技能搜索正常，MCP 注册直接进入精简表单，默认仅名称、地址、鉴权方式；高级设置可正常展开。
- 在最终镜像上实际打开 MCP 注册及高级设置，没有页面错误；代码目录默认保留对话，点击 `agent.py` 展开右侧源码，顶部/上下栏对齐。
- 文件按钮打开中间目录，点击 `AGENTS.md` 显示深色源码预览；收起恢复未发送测试文字。测试文字已清除，没有发送对话或修改草稿配置。当前用户草稿 r4 保持不变。
- 已查看当前 Python 算子的单列 Schema / 源码编辑器，无窄抽屉双列挤压。
- 最终 Web Build ID：`0KrlJ9xu-3QuaFN0ZqWGy`；Web/API/Worker 均 healthy；登录页与 API health 均 HTTP 200。
- 173 当前没有可用知识库和 MCP 服务器，本次验证其空状态和注册入口，没有声称验证真实资源绑定。

## 回滚

只将 compose 的 `services.web.image` 改为HTTP 兼容修复前的 `kai/axis-web:evolution-builder-0d6cf694`（该版本在 HTTP 页面打开 MCP 注册会报错），执行 `docker compose -f compose.json up -d --no-deps --no-build --wait web`。若需要回到本次全部调整之前，使用 `kai/axis-web:evolution-restore-8a8ec537`；备份位于 `backups/builder-layout-9ac430c6/`。

## HTTP 注册兼容修复

实际浏览器验证发现，173 的 HTTP 页面不提供 `crypto.randomUUID()`，导致 MCP 表单初始化触发错误边界。改用 HTTP 页面可用的 `crypto.getRandomValues()` 生成引用标识，并在同样缺少 `randomUUID` 的测试环境中覆盖注册流程。
