# 全局动效优化与 173:3302 验证（2026-09-24）

## 变更

基于已合并的 `develop` / `auto/agent-evolution` 提交 `6d28480c`，在独立 worktree 中完成全局动效优化，代码提交为 `c10f9d6a562c18546bac0eab6020cae240df5198`。

- 在 `codex-theme.css` 增加统一动效契约：control `160ms`、content `220ms`、panel `280ms`，以及统一 easing。
- 为 account popover、任务侧栏、详情面板、文件 rail、activity/tool/agent 行补充克制的 opacity/transform 入场与完成反馈。
- 在全局 reduced-motion 规则中明确关闭新增动画和 transform，保留语义状态的颜色、图标和文案。
- 未引入第三方 motion 依赖，未对流式 token 逐字增加动画，未改变业务状态机。

## 本地验证

运行位置：独立 worktree `/tmp/agent-studio-motion-6d28480c`。

- `npm test -- --run tests/motion-system.spec.ts`：3/3 通过。
- `npm test`：全量通过，保留仓库既有 1 个 skip。
- `npm run build`：Next.js 16.3.3 production build 通过。
- `git diff --check`：通过。

## 173 部署

目标：173 隔离验证栈 `agent-evolution-173`，Web `3302`；未操作 3301。

- 部署前只读核对：API/Worker/Web healthy；API `/healthz` 200；Web `/login` 200；无进行中的 Run，数据库仅有历史 `timed_out` 记录 1 条。
- 仅替换 Web，API/Worker 保持 `develop-20260924-d9eec75b`，避免无关重建。
- Web 镜像：`kai/axis-web:evolution-motion-6d28480c-r2`。
- Web build id：`8xzEwGJXc2nHV3KR7H5pp`。
- 回滚快照：服务器 `/data/agent-studio-evolution-20260920/backups/motion-6d28480c-r2/compose.json`。
- 切换后：Web/API/Worker healthy；3302 `/login` 200；8802 `/healthz` 200。
- 3302 实际 CSS chunk `_next/static/chunks/29nqp_blvllk2.css` 含 `--codex-motion-panel:.28s`，证明新 CSS 已服务。

第一次尝试因 web-runtime 包目录多了一层未切流，第二次修正后 Linux runtime smoke 与 canary `/login` 200，再完成切换；3302 未经历不健康状态。

## 浏览器验收

使用 ZCode in-app browser 打开 `http://172.20.109.173:3302/login`：

- 登录页实际加载成功，页面 title 为 `KAI WORKBENCH`。
- computed motion tokens：control `.16s`、content `.22s`、panel `.28s`。
- 实际 stylesheet 可读取 `codex-surface-in` 与 `codex-motion-state` 规则。
- 已使用授权的工作区账号完成登录，进入任务工作台；任务列表、项目、知识库/智能体/自动化任务/技能导航均正常渲染。
- 打开账户菜单后，线上 computed style 确认 `codex-surface-in`、`0.22s`、token `--codex-motion-content: .22s`，证明 popover 入场规则在 3302 实际生效。
- 已保存浏览器验收截图：`/Users/xiaokai/.zcode/cli/artifacts/sess_e91702fa-5ece-4d50-9acb-4111556f17cf/call_HTBlkUE4oPApWHWTParMEHVe-tool-result-bfbcfe3c-6dfd-4b02-a841-33226b2ac3e3.png`。
- 浏览器运行时未提供系统级 `prefers-reduced-motion` 切换能力，因此未把页面注入样式当作系统级验收；reduced-motion 仍以代码级规则、定向测试和生产构建为依据。
