# KAI 品牌与对话交互发布 · 20260906-132229

地址：http://172.20.109.174:3501

## 指定任务故障

任务：涉非舆情分析流程与数据统计。
Thread：bfcf96bd-304a-4d1e-b197-c0c2d5331936。
Run：run_8b0a676ebd3940918a6484cf07047503。

2026-09-06 00:32:16 UTC 开始；00:32:32 接收到“涉非的”引导；00:33:09 最后一段回复完成；01:32:41 被 stuck_running_reaped 标记 timed_out。历史事实保持原状。

根因：Claude 引导适配器按 query() 次数累加应收到的 ResultMessage，假设每条输入独立结束。SDK 在工具执行期间可能将补充合入当前轮，因此只有一个 ResultMessage，计数永远不归零。简单改成第一个 ResultMessage 结束也不正确：生成期间收到的补充可能在第一个结果后才被 SDK 消费。

修复使用 --replay-user-messages 的 UserMessage 输入确认判断消费时机：同轮确认后正常收束；上一轮已完成但仍有未确认输入时等待确认，再等后续轮结束。只在 SDK 确认后确认引导成功。完成后缺少确认最多等待 10 秒，补充保留队列，不再无限等待。未开始发送的边界输入可恢复，异常与取消均关闭接收通道。

依据：[Claude CLI 输入确认](https://code.claude.com/docs/zh-CN/cli-reference)、[Codex 补充与排队](https://learn.chatgpt.com/docs/prompting)。

## 品牌及交互

- 用户标志经 imagegen 提取为透明图标；去除小尺寸文字，保留轮廓与青色细节。
- 网页标签、右上角、共享品牌组件和路由加载使用同一图标；深色主题使用浅色轮廓，加载尊重减少动态效果设置。
- Docker 两种 Web 构建方式均复制 public 静态资源，避免本地正常而部署后图片 404。
- 任务工作区移除产品使用手册；修正浅色分栏文字和边框对比度。
- 个人设置→配置可选择补充默认排队或调整方向；Enter 使用默认行为、Alt Enter 临时切换、Shift Enter 换行。
- 运行中有输入时可点击发送/引导，停止操作仍可用。附件与暂不可引导情况保留在队列。
- 队列原位编辑、取消编辑、上下排序、发送、引导、删除，保留消息 ID、附件与位置；引导确认中禁改对应消息。
- 技能菜单只列当前智能体已发布版本携带的技能及技能创建入口，支持名称和描述搜索，不读取其他任务文件。
- 简化空白任务标题，保留 800px 对话内容宽度、顶部运行用时、操作栏 HH:mm、过程折叠、中文输入保护及任务文件隔离。

对齐依据为用户截图与当前官方文档；Codex 桌面应用禁止自动读取自身界面，不声称逐像素核对桌面最新版本。

## 验证

- 前端 74 个文件 / 488 项通过，生产构建通过。
- 后端 257 项通过（Runtime、引导、技能投影、任务文件和 Studio API）；Ruff、Pyright 通过。
- 新回归覆盖：引导合并只发一个结果、先结果后消费引导、缺少消费确认不挂起、未送达输入保留、队列原位编辑/排序/删除与确认期间保护。
- 174 实际 SDK 0.2.152 + deepseek-v4-flash：生成中插入 9.7 秒完成，工具执行中插入 8.5 秒完成。均确认引导，最终回复包含指定验收标记，正常关闭，使用隔离 SDK 会话，无用户任务历史改写。
- 本地浏览器：明暗图标、中文输入、按钮入队、编辑保存、引导确认、默认引导配置、刷新队列恢复、文件分栏及完成操作栏。窄屏检查工具未应用 viewport override，不能据此声称手机视觉验收通过；响应式规则保留。
- 预发布 3502 验证首页、icon.svg、品牌 PNG、鉴权配置和 runtime config 均 200，容器 healthy / restart 0。

## 部署及回滚

源码：/data/kai-release-20260906-132229。
Web：kai/axis-web:20260906-132229；容器 axis-web-20260906-132229。
Web image ID：sha256:e0d3339ebac0809a39da9092b2063fb829ec695a3ea4c909f6609e21e73c4b6a。
API、3 个 Worker、quality-sync：kai/axis-api:20260906-132229。
API image ID：sha256:566441d3cbdd9fb1dab3558675929be4fb2bd76d9f210ea9350f701e10b4a193。

使用现存已验证依赖镜像构建，无依赖升级、数据库迁移、智能体版本更新。发布前活跃 Run 为 0。原 3301 Web 保持原状。

Web 回滚：停止 axis-web-20260906-132229，启动 axis-web-20260906-014427。
API 回滚：在 /data/agent-studio/docker-compose 使用 compose.yaml、compose.harbor.yaml、compose.axis-release-20260906-014427.yaml，profile observability、env .env.production、project agent-studio-174 执行 up -d --no-deps --no-build --scale worker=3 api worker quality-sync。回滚前检查活跃任务。

最终正式环境验收：首页、图标、品牌资源、鉴权配置、runtime config、API healthz 均 200；Web、API、3 个 Worker、quality-sync 均 healthy，restart 0；预发布容器已清理。
