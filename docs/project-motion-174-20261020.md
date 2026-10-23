# 项目任务过渡与顶部精简：174 发布

源码提交 `8c264832`、`fe243c5a`。Web 镜像 `harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:project-motion-20261020-r41`，ID `sha256:97a50a93132131453ba618956b8aa1746c870f30302b511855eb0e41d8515c21`，12 层。HTTPS/3301 和 3501 均已更新，两个 Web 容器 healthy、restart=0。

## 行为

移除项目顶部整条“项目 / 刷新 / 项目设置”工具栏。项目设置由左侧项目更多菜单进入，关闭弹窗时清除 settings 查询参数，允许再次打开。

项目与主对话输入框去除阴影、焦点光晕，保留中性边框。项目发送后通过浏览器 View Transition 收起右侧资源栏，并把输入框平滑衔接到主对话底部；左侧导航保留。主对话完成历史初始化、恢复草稿后才能结束过渡并提交任务，避免历史导入清空待发送内容。动画结束前不执行发送；离开目的页面不发送，已有防重机制继续生效。减少动态效果偏好或不支持 View Transition 时直接跳转；过渡超时释放画面。

## 验证

55 项相关测试通过，Next.js 16.3.8 / TypeScript 生产构建通过。真实 Chrome 验证项目设置菜单、顶部工具栏移除、输入框 computed box-shadow 为 none，以及项目启动后的输入框保持底部。

HTTPS 实际任务验证：[浏览器记录](results/project-motion-174-20261020/browser.jsonl)。目的页 211ms 内就绪，动画约 382ms 完成，随后只发送一次任务；真实回复“动画验收完成。”成功显示，无页面 JavaScript 错误。预览阶段额外通过持久化历史确认任务 succeeded。3501 页面回归亦通过：标题编辑与取消、版本历史提示移除、项目展开/收起、菜单入口、卡片引导以及右栏滚动时输入框不移动。验收临时项目删除，测试任务归档，测试会话撤销，预览容器及临时凭据文件清理完成。

本次仅更新 Web；API、Worker、数据库及模型配置未变。使用锁文件匹配的构建环境及已有 Linux 运行依赖，package.json 哈希与线上匹配，基于 r35 构建以避免镜像持续叠层。

## 回滚

发布目录 `/data/agent-studio/releases/project-motion-20261020-r41`；compose 发布前私有备份为 `web-r41-*.before.private.*`。上一版 Web r40 保留；3501 回滚容器为 `axis-web-project-ui-20261020-r40-rollback-project-motion-20261020-r41`。
