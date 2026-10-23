# 项目菜单与图标精简：174 发布

源码 `522a87f9`。镜像 `harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:project-actions-20261020-r42`，ID `sha256:93f342e700cfd95b9d219e99dae94c9c6b315bd85a5fcd633f85c86ced00f77d`，12 层。HTTPS/3301、3501 已更新，两个 Web 容器 healthy、restart=0。

项目菜单移除“项目设置”“新建任务”，保留“查看项目 / 置顶或取消置顶 / 移除项目”。移除项目行后的成果快捷入口，缩小标题预留操作区，保留右侧加号。项目页右侧成果卡片不变。侧栏加号及项目输入框“+ / @”去掉默认、悬停背景与阴影，保留键盘焦点反馈。

25 项已有项目和侧栏回归测试通过，Next.js 16.3.8 生产构建及 TypeScript 检查通过。真实 Chrome 在预览及 HTTPS 正式页面检查菜单项、成果快捷入口缺失、浅/深色选择器下默认/悬停的 computed background 均为透明、box-shadow 均为 none；资源与知识库弹窗正常，无页面 JavaScript 错误。[浏览器记录](results/project-actions-174-20261020/browser.json)。正式页面截图人工检查通过。

仅更新 Web，API/Worker 不变。发布目录 `/data/agent-studio/releases/project-actions-20261020-r42`，回滚配置 `web-r42-*.before.private.*`；3501 回滚容器 `axis-web-project-motion-20261020-r41-rollback-project-actions-20261020-r42`。测试临时项目删除、测试会话撤销、预览容器及临时凭据清理。
