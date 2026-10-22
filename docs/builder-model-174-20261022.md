# 构建助手独立选择模型 · 174

源码 `24e49c1e`，输入框加载状态修复 `1d111bc3`。API 发布 `builder-model-20261022-r54`，Web 发布 `builder-model-20261022-r55`。

## 行为

构建助手输入框复用主对话的模型列表与选择控件，允许独立选择本轮构建/对话模型。原先预览线程只注入草稿默认模型且禁用选择，紧凑输入框又隐藏了模型控件；现在通过会话作用域显式开启选择，其他预览作用域保持原行为。内嵌构建页和独立构建弹层均接入。

模型选择保存在当前用户、当前草稿独立的浏览器偏好中；不会与主对话的模型偏好混用，不修改草稿的 model 或 revision。默认优先使用通用 Flash 路由，当前选择仍可用时保留，不随草稿默认模型变化。DeepAgents 支持两种文本协议；Claude/Codex 按对应执行协议过滤可用模型，视频路由不进入构建对话选择。

新建草稿、修改建议（包括格式修复和后续 Skill Creator）、附件读取、实际试跑均传递所选模型。试跑请求显式 `modelRouteOverride` 优先于上轮路由；未传字段的旧客户端继续继承原路由，显式 null 可清除继承。服务端检查当前租户模型配置、凭据、运行协议与必需能力，禁止通过 Agent 绑定静默换回默认模型。模型选择不会改变智能体空间的配置、工具权限或发布状态。

模型目录读取属于输入配置加载，不计入消息运行状态。已补上模型目录慢响应与空白对话的回归测试，避免临时空消息被撤下时触发 assistant-ui 消息索引错误。

## 验证

前端相关 50 项测试与 Next.js 16.3.8 / TypeScript 生产构建通过。覆盖控件展示、默认模型去重、独立选择、构建请求、实际运行请求、连续对话中途切换、输入保留和浏览器偏好。Python Ruff 与 103 项接口/服务回归通过。

在 174 使用临时 QA 草稿通过真实构建输入框发起两轮 Worker 任务：智能体配置默认 `deepseek-v4-pro`；第一轮选择 `deepseek-v4-flash`，`model.route.selected` 实际为 Flash，并记住测试口令；第二轮切到 Pro，保持同一会话，正确回忆口令。两轮 succeeded，草稿默认模型及 revision 不变，刷新后模型偏好保留。临时草稿在测试完成后删除，没有改动用户智能体。

预览和正式 HTTPS 入口均完成上述两轮真实执行；3501 验证控件可用、刷新保持选择、切换不清空输入和不修改智能体配置。浏览器刻意延迟模型目录响应，覆盖慢加载时序。测试操作使用独立浏览器上下文，不修改用户页面、剪贴板或真实智能体，临时草稿完成后删除。

记录：[预览](results/builder-model-174-20261022/preview.jsonl)、[HTTPS](results/builder-model-174-20261022/https.jsonl)、[3501](results/builder-model-174-20261022/3501.jsonl)。

## 部署

API 镜像 `kai/projects-api:builder-model-20261022-r54`，ID `sha256:67236d339574a8cd6666a8c259f6b55e3d20eda1143f74a291c454a41f51e91b`，3 层，revision=`24e49c1e`。

Web 镜像 `harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:builder-model-20261022-r55`，ID `sha256:47fe1e8c4f321d2bd30d4c3985f46e9ed5466ea6f53b0673b84a0a63d03919ff`，12 层，revision=`1d111bc3`。正式 Web 和 3501 均 healthy、restart=0。API healthy、restart=0；3 个 Worker 继续使用 r45，未重建或重启。数据库版本继续为 0043。

Web 发布目录 `/data/agent-studio/releases/builder-model-20261022-r55`，配置备份 `web-r55-runtime.before.private.json`、`web-r55-compose.before.private.yaml`；3501 稳定上一版容器 `axis-web-link-preview-20261021-r53-rollback-builder-model-20261022-r55` 保留。API 发布目录为 r54，对应原配置保存在该目录 `runtime.before.private.yaml` 和 `compose.before.private.yaml`。

预览容器、转发和临时凭据在验收后清理，测试登录撤销。没有改动共享出网代理、SDK、模型网关或智能体默认模型配置。


## 推荐标记补充 · r56

源码 `e79867b1` 为构建助手默认推荐模型追加“（推荐）”，标记按可用模型与运行协议计算，优先 Flash；切换当前选择不会改变推荐项。内嵌构建页和独立构建弹层均接入，不修改智能体配置或主对话的选择行为。

前端原有 50 项相关测试与生产构建通过。正式 Web、3501 使用临时草稿验证推荐标记唯一、切换后仍标记原推荐项、刷新保留模型选择、未发送输入保留、智能体默认模型和 revision 不变。此补充只涉及展示，未重复发起模型推理。验证记录见 `results/builder-model-174-20261022/recommended-web.jsonl` 与 `recommended-3501.jsonl`。

Web 发布 `builder-model-recommended-20261022-r56`，镜像 ID `sha256:8091fc6a6da19d77e3572768d65dc137581c10ac4a4937bc63da0641e7401046`，12 层，revision=`e79867b1`。API、Worker 保持原版本。发布目录 `/data/agent-studio/releases/builder-model-recommended-20261022-r56`，配置备份 `web-r56-runtime.before.private.json`、`web-r56-compose.before.private.yaml`，3501 回滚容器 `axis-web-builder-model-20261022-r55-rollback-builder-model-recommended-20261022-r56`。
