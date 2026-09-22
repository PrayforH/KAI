# 173：主对话「文件与知识」合并成一个添加入口

2026-09-22 部署到 173 演化栈（`agent-evolution-173`，8802/3302）。源码提交 `d1cc27f9`，
web 镜像 `kai/axis-web:evolution-add-icon-d1cc27f9`。api / worker 未动（`evolution-d6bada40`）。

## 改了什么

主对话工具栏原来有两个入口服务同一个决定：回形针管文件，`@` 下拉里放知识库勾选 —— 一轮对话的
知识库绑定成了"两个图标里的一个"，而不是"这一轮带了什么"的一部分。

现在是一个图标，点开**向侧边展开**，面板里两段：

- **文件**：`ComposerPrimitive.AddAttachment`（与粘贴走同一条附件通道）；
- **知识库**：勾选即绑定，仍走 `useTaskKnowledge().toggle()`。

顺带两件事：

- 勾选列表体抽成 `KnowledgeBasePicker`，合并面板与带标签的上下文控件共用一份"搜索/勾选/清除"；
- **嵌入作用域（`conversationScope`）的语义保留**：作用域下知识库属于 Agent 配置，面板里那一段
  换成「配置智能体知识库」（调 `conversationScope.onConfigureKnowledge`），此时不显示数量角标；
  `compactComposer` 时保留文件、隐藏知识库段。切换器/模式/模型那几个控件仍在原来的
  `!compactComposer` 守卫里。

## 验证

| 检查 | 结果 |
| --- | --- |
| 本地全量前端测试 | 130 文件 / 824 passed，1 skipped；`tsc --noEmit` 干净 |
| 新增控件测试 | `tests/composer-add-control.spec.tsx` 6 例：一个图标、勾选即绑定、角标计数、Escape 归位、锁定态、作用域动作、compact 隐藏知识库 |
| 镜像构建自检 | `node -e 'require("rxjs"); require("tslib")'` → OK |
| 产物核对 | web 容器内 chunk 命中 `添加文件或知识库`、`composer-add-trigger`、`配置智能体知识库` |
| 服务 | api/worker 保持 8 小时未重启且 healthy；web healthy；8802 healthz 200、3302 首页 200 |
| 在飞运行 | 无（`runs` 里只有 succeeded/failed/timed_out；部署只 `up -d --no-deps web`，不碰 worker） |

## 发布方式（这次用的）

173 的 web 发布是"本地构建、173 只做 COPY 层"：

1. 本地 `npm run build`（`output: "standalone"`），组 `web-runtime/`：`server.js`、`package.json`、
   `public/`、`.next/`（含 static），**不带 `node_modules`**——保留基础镜像的 Linux 依赖（约 47MB）。
2. 打包时用 `COPYFILE_DISABLE=1 tar --no-xattrs`，否则会带进 macOS 的 `._*` 条目。
3. `scp` 到 `/data/agent-studio-evolution-20260920/`，在该目录跑部署脚本（照 `deploy-chat-fix.sh` 的形状）：
   `docker build --build-arg BASE_IMAGE=<当前 web 镜像>` → smoke test → 备份 `compose.json` →
   **容器内** python 改 `compose.json`（173 主机没有 python3）→ `docker compose -f compose.json up -d --no-deps web`。
4. 部署脚本里对 `compose.json` 的项目名与"当前 web 镜像"加了断言，避免停错栈或覆盖别人的 tag。

## 回滚

`/data/agent-studio-evolution-20260920/backups/add-icon-d1cc27f9/` 里有改前的 `compose.json`；
把 `services.web.image` 改回 `kai/axis-web:evolution-f77d6492`，再 `docker compose -f compose.json up -d --no-deps web`
即可。

## 一处需要说明的偏差

同一份改动我先落到了 `develop` 并部署到 174（提交 `3f58c360`，tag `develop-20260921-3f58c360`），
方向与既定流程（173 迭代 → 合适后整体合入 develop → 部署 174）相反。本文件记录的是它在 173 上的落位；
提交内容与 develop 上那份一致（多出的只是作用域模式的处理）。develop/174 是否需要回退由用户决定。
