# agent-evolution 173 修复批次部署（2026-09-22）

在 `auto/agent-evolution` 上合并 develop、评审分支代码，并修复用户报回的 7 项中的前 3 项，
部署到 173 隔离验证栈（`agent-evolution-173`，API 8802 / Web 3302）。

## 1. 版本与镜像

| 项 | 值 |
| --- | --- |
| 分支合并 | `a5f4d36a`（merge develop；两个冲突见下） |
| 修复批次 | `01f1f3cb`（#1/#2/#3）+ `322bd67a`（测试跟进）+ `a65b4117`（#1 的 CSS 兜底） |
| api / worker | `kai/axis-api:evolution-01f1f3cb`（`e0407612b879`） |
| web | `kai/axis-web:evolution-01f1f3cb-webfix`（`3ad4ba54935a`） |
| 上一版本 | `evolution-0f6149cb`（api/worker/web 三个 tag 都在，可直接回滚） |

合并冲突两处，两边都取「更新的那一侧 + 保留 develop 的意图」：`tests/unit/test_final_readiness.py`
取 develop 的 `_newest_migration_revision()`（手写 pin 每次迁移都会过期）；`agent-builder-overlays.tsx`
保留分支重构后的渲染树，并把 develop 的 `isTerminalRunStatus` 用到存活的那一行。

## 2. 本批修复

**#1 构建助手「处理过程」与正文间隔过大** — 根因是一个**空容器带边距**：`agent-playground-thread.tsx`
的 `afterMessage` 把回调内容包在 `.turnExtensions` 里，即使什么都没渲染出来，容器仍占
`margin:12px 0`。浏览器实测：处理完成块底 280 → 正文顶 311，**31px 全是空隙**
（7px 自外边距 + 12px + 12px）。修复分两层：组件在 `extra`/`approvals` 都为空时返回 `null`；
CSS 加 `.turnExtensions:empty { display:none }` 兜住「传进来的元素渲染成空」的情况。
复测：间隙 **31px → 7px**（只剩该块自身外边距），容器 `display:none`。

**#2 布局与入口** — 四项都已在 3302 上目视确认：

- 配置面板「文件与知识」行改用**展开态文件夹**图标（新增 `knowledgeOpen` 字形）并移到行右端
  （新增 `.configTrail`，与 `.chevron` 同尺寸对齐右边缘）；
- 「新建对话」与「会话下拉」合为一个菜单：摘要显示当前会话，面板内依次是「＋ 新对话」、
  历史会话（行，不再用原生 select）、评测用例、修订信息；
- 去掉「对话选项」的 `···`（其内容并入上面的菜单，`.moreSummary` 样式一并删除）；
- 顶栏「代码」移入**配置面板头部**（`代码` 与 `保存` 并列），顺带让此前声明却从未使用的
  `onCode` prop 真正生效。

**#3 技能创建失败** — 真任务 `run_988213c9536f49ed82a2ff4a1b7d059f` 的 Worker 日志显示
`TimeoutException`，消息体是 openresty 的 **504 HTML 页**。根因：模型发出的沙箱命令在
`e2b.py` 里用 `commands.run(..., timeout=0)` 启动，**没有传 `request_timeout`**，于是继承了
CubeSandbox 控制面的 30s（`cubesandbox.py:192-199`）——而 e2b SDK 把该值当作**打开 envd 流**
的截止时间。流打开慢于 30s 时数据面代理返回 504，SDK 抛出携带 HTML 的 `TimeoutException`，
Run 以不透明的 `runtime_error` 失败。修复沿用同分支 `_COLLECT_REQUEST_TIMEOUT_SECONDS`
的做法：新增 `_COMMAND_REQUEST_TIMEOUT_SECONDS = 900` 并传给命令路径；命令自身的预算仍由调用方
的 `timeout_seconds` 决定（`timeout=0` 不变），新常量也远低于 `_keep_alive` 续期的沙箱 TTL。
新增 `tests/unit/sandbox/test_e2b.py::test_remote_session_sets_its_own_request_deadline`。

## 3. 验证证据

- 代码：`tsc` 无错；vitest **811 passed / 1 skipped**；`pytest tests/unit` **1473 passed**；
  `pytest tests/unit/sandbox` **170 passed**。
- 镜像内对账（`find . -type f | LC_ALL=C sort | xargs sha256sum | sha256sum`）：
  上传包 `1ce15e15…` == api 镜像内 `1ce15e15…`（与本地 HEAD 同源）；
  web 镜像内 `BUILD_ID` 与本地构建一致，静态包含新标记「会话：新建或切换」。
- 运行时：api/worker/web 三容器 healthy，`/healthz` ok，3302 `/` 200；
  真实任务在页面上跑通（`处理完成 · 持续了 1s` + 正文，事件链完整）。
- 目视：#1 间隙截图前后对比、#2 四项在截图中逐一可见。

## 4. 回滚

```bash
ssh 173 && cd /data/agent-studio-evolution-20260920
# 本批次：backups/01f1f3cb-webfix/（web）与 backups/01f1f3cb/（三件套）
cp -p backups/01f1f3cb/compose.json compose.json   # api/worker/web 都回到 evolution-0f6149cb
docker compose -f compose.json up -d --no-deps api worker web
# 只回滚 web：把 compose.json 的 web image 改回 kai/axis-web:evolution-01f1f3cb 再 up -d --no-deps web
```

## 5. 未完成（用户清单中的后四项，下一步）

- **#4** MCP 从主页「插件」移除、改为智能体私有资产配置；
- **#5** 参考 agenta 简化智能体配置下方「工具 / 高级设置」抽屉；
- **#6** 抽屉等整体动效优化；
- **#7** 智能体配置的 Skill 支持勾选（后端已有 `GET /v1/studio/skills/catalog` 与
  `PUT /drafts/{id}/skills/references`，可直接支撑勾选式引用）。

评审（`docs/agent-evolution-code-review-20260922.md`）里的 P0-1（`_save` 走
`model_copy` 绕过校验）、P0-2（5 处 CAS 只判 revision）、P1 系列也仍待处理。
