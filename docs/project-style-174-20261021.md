# 任务菜单与项目空间样式：174 发布

源码 `c507fca5`，Web 发布 `project-style-20261021-r48`。

## 修正与调整

任务行菜单原先套用通用资源菜单：宽 272px、行高 44px、字号 14px、图标 20px。现在侧栏及项目任务菜单与 header 共用密度参数：宽 208px、行高 32px、字号 12.5px、图标 15px，圆角 10px。触发按钮的省略号仍使用同一 Codex 资产；侧栏保持 24px 点击区域，header 和项目列表保留 32px 点击区域。任务菜单的副本、重命名、分支、置顶、项目移动与归档语义沿用原实现。

项目独立整理页沿用原有布局，任务执行继续复用主对话。缩小项目标题及图标、收紧标题与最近任务间距；最近任务移除逐行边框，使用轻量悬停状态，置顶文案统一为“已置顶”。

右侧卡片统一 12px 圆角、16px 内距、13px 标题与细边框，移除连接器的额外灰底，列表、指令预览与按钮采用一致的字号和间距。连接器数量并入标题，文件添加、展开与箭头使用 SVG，已有资源继续隐藏空状态引导。文件、技能、知识库、成果分组与操作入口保留。资源弹窗减轻遮罩和阴影，主按钮、搜索框和底栏适配主题颜色。

## 验证

43 项回归测试通过：task-sidebar-status、task-header-actions、project-home、nested-menu、project-task-transition。Next.js 16.3.8 / TypeScript 生产构建通过。

浏览器使用隔离的项目、任务、文件与成果响应夹具，检查实际组件与 CSS，不新增项目或运行模型任务。三处任务菜单的实测行高、字号、图标分别为 32px、12.5px、15px。验证右侧独立滚动、底部输入框位置固定、技能下拉与指令编辑弹窗可用；检查深浅色截图及 390px 窄屏无横向溢出。

[改动前](results/project-style-174-20261021/before.jsonl)、[预览](results/project-style-174-20261021/preview.jsonl)、[HTTPS 正式入口](results/project-style-174-20261021/https.jsonl)、[3501 入口](results/project-style-174-20261021/3501.jsonl)。

仅更新 Web；API/Worker、数据库及执行配置沿用原部署。发布目录 `/data/agent-studio/releases/project-style-20261021-r48`，配置备份 `web-r48-*.before.private.*`，3501 上一容器 `axis-web-sidebar-pin-20261021-r47-rollback-project-style-20261021-r48` 保留供回滚。

Web 镜像 ID `sha256:27f785b6ce853c267e2ae15636d23aa4f9bafaae4ce835d067ed8bcb9cc7455a`，12 层，revision=`c507fca5`。两个 Web 容器 healthy、restart=0；验收登录会话、临时预览容器及凭据已清理。
