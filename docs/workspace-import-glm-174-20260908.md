# 工作台宽度、GLM 视觉与 NexAU 导入修复

发布版本：20260908-101112。访问：http://172.20.109.174:3501/studio/agents。

## 变更

- 右侧效果测试输入框与首页一样，实际输入框最大宽度为 800px，居中显示；含两侧 14px 留白的容器为 828px。窄栏内自动收缩。
- 左侧构建栏最大宽度从 480px 放宽至 720px，中间资产栏从 420px 放宽至 640px。保留最小宽度和默认宽度，宽屏拉伸时为右侧预留空间。
- 将生产租户 local 的 glm-5-3-flash 从 chat 修正为 vision，保留原地址和凭据。移除 glm-5-2 及其已存储凭据；删除前确认没有草稿引用，通过正常模型服务执行并记录审计。默认目录与新部署策略也不再种入 GLM-5.2。
- 修复直连 Anthropic 兼容接口时漏掉 `/v1` 的问题。GLM 配置的 SDK base `/api/anthropic` 现在发送至 `/api/anthropic/v1/messages`；已有 `/v1` 不会重复追加。
- NexAU 导入支持 ZIP、RAR4 和 RAR5；通过 libarchive 内存解压，检查路径、链接、重复项、文件数和解压体积，不执行包内代码。
- 自动识别 agent.yaml、agent.yml、code_agent.yaml 等实际 Agent 配置文件，包括包外层目录；排除被子 Agent 引用的配置以定位根 Agent。支持内联 system_prompt，以及没有 Skill 的合法配置。
- 导入文件选择器移出会关闭的菜单，修复选择器打不开的问题。提供可见的进行中、失败、兼容警告和成功提示；失败后可重试同一个文件。成功后直接打开导入草稿的构建工作台，不自动试跑；列表刷新失败不会误报导入失败。

## 验证

- 前端 80 个测试文件、520 项测试通过，Next.js 生产构建通过。
- 最终后端相关回归 135 项通过，覆盖 NexAU/RAR 导入、目录、模型请求、编译和 Studio API。Ruff、Pyright 通过。
- 浏览器在本地、174 预览容器和正式版本检查：实际 800px 输入宽度、720/640px 分栏上限、窄屏、深浅主题、两侧附件、图片原图、停止发送互斥、索引、多轮对话、原生文件选择器、错误可见及同文件重试。浏览器 API 使用隔离 fixtures，不修改用户业务数据。
- 174 新 API 镜像真实读取 RAR4 NexAU 包并保存可读草稿，测试草稿删除；libarchive 官方 RAR5 压缩夹具解压通过。
- GLM 真实视觉请求返回“左图：红色正方形；右图：蓝色圆形。”
- 完整 Worker 图片试跑使用 glm-5-3-flash，状态 succeeded，正确回答红色正方形与蓝色圆形，无模型替换。运行 ID：run_c4b23e56411e42c69671a48d5cfbe905；一次性 QA 草稿已删除。
- 删除 GLM-5.2 后再次读取模型列表，只有 deepseek-v4-flash、deepseek-v4-pro、minimax-m3 和 glm-5-3-flash；后两者为 vision。

## 部署

发布目录：`/data/kai-import-20260908-101112`。

- Web：`kai/axis-web:20260908-101112`，SHA `33b87f6e043927d7e99b62b33a5acec79dda66070bf30805ca2fa97e05a9a28f`。
- API、3 个 Worker、quality-sync：`kai/axis-api:20260908-101112`，SHA `85f22d4268fc0155d7055ee9be9eac17a368f26381a99c05e7fa80db9c0ecd3d`。
- 后端基于上一版本定向更新 studio/api.py、bundle_import.py、model_configuration.py、catalog.py、deployments/models.py；新增 libarchive-c 5.3 和系统 libarchive13。常规 Dockerfile、pyproject.toml、uv.lock 同步依赖。
- Compose 新叠加文件：`compose.axis-import-20260908-101112.yaml`。无数据库迁移。部署前检查无活动运行，保留上一版 20260908-083442 镜像和 Web 容器。
- 回滚后端时旧版默认目录可能重新种入 GLM-5.2；如需回滚，应同时保留本次目录修正，避免重新引入已删除模型。

## 兼容范围

这是 NexAU 配置到当前 Studio 草稿的兼容导入。不能自动映射的框架工具、原生子 Agent 等会显式列出警告，需在配置中绑定；不能保证任意 NexAU Python 项目可原样执行。不支持加密、分卷或损坏 RAR。尚未收到用户失败的原始压缩包，本次以真实 NexAU 结构夹具、API 持久化和官方压缩 RAR5 夹具验证。

部署后最终检查：6 个应用容器全部 healthy、restart=0；主页、登录、智能体管理、认证配置、运行配置均返回 HTTP 200。正式版本浏览器回归通过。
