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

## 5. 第二批：评审 P0 与 #4（同日夜）

| 项 | 内容 |
| --- | --- |
| 版本 | `ef129d81`（评审 P0）、`e2ab001a`（#4 MCP） |
| 镜像 | api/worker `kai/axis-api:evolution-e2ab001a`，web `kai/axis-web:evolution-e2ab001a` |
| 回滚点 | `backups/e2ab001a/compose.json`（回到 `evolution-01f1f3cb` + web `-webfix`） |

**评审 P0-1（写路径绕过校验）**：`evolution/service.py` 的 `_save` 是唯一写入路径，原先用
`model_copy`（跳过全部 validator）。改为 `model_validate` 校验下一个状态，状态字面量之外的
值不再落库、也不再等到读回时才以 500 暴露。

**评审 P0-2（活性判据只判一半）**：`_active()` 同时判 revision 与「已停止/已过期」，
而 `rollback`/`set_experience`/`observe` 各自手写了只判 revision 的副本——已取消或已过期的
job 仍可被修改。三处改为共用 `_active`；`refresh` 保留「非 active 时优雅返回」，`cancel` 保留
只判 revision（取消本身就是要做的状态迁移，且文档写明「重试即完成取消」）。
新增测试 `test_workflow.py::test_stopped_job_rejects_mutation_and_the_write_path_validates`
覆盖取消态、过期态与非法状态三条断言；`pytest tests/unit` 1475 passed。

**#4 MCP 从「插件」改为智能体资产**：原先 MCP 是主页一个顶级工作区（`/studio/capabilities`
的 `McpCatalogControlPlane` 负责注册、发现、凭据、目录），而智能体只能从结果里挑。现在：

- 工作区导航去掉「插件」（`workspace-navigation.tsx`、`task-sidebar.tsx` 的 visible 列表），
  `/studio/capabilities` 改为 `redirect("/studio/skills")`，老书签仍可用；
- 「技能」区只剩一个 section，`StudioCapabilityManager` 与 section 导航去掉 MCP 一半；
- **注册能力仍有着落**：智能体的「MCP 配置」组新增「管理 MCP 服务器」，在同一处以侧栏打开
  原控制面（`McpCatalogControlPlane` 动态载入），与决定「本智能体可用哪些 MCP」的勾选列表相邻；
- 智能体工作区页脚指向已下线页面的「模型与集成设置」链接删除。

验收：镜像内静态包含「管理 MCP 服务器」标记；访问 `/studio/capabilities` 服务端不再返回
MCP 目录内容、只带 `/studio/skills` 的跳转（Next 的 RSC 跳转是 200 + 客户端跳转，故状态码仍为
200，判据看内容）；vitest 128 文件 / 811 passed。

## 6. 第三批：#5 / #6 / #7（同日夜）

| 项 | 内容 |
| --- | --- |
| 版本 | `53d4e750`（#7）、`d6bada40`（#5/#6 + 模板 domain）、`394c6b75`（面板渲染测试） |
| 镜像 | api/worker `kai/axis-api:evolution-d6bada40`，web `kai/axis-web:evolution-d6bada40` |
| 回滚点 | `backups/d6bada40/compose.json`（回到 `evolution-e2ab001a`） |

**#5 配置面板抽屉做减法**：工具组原来把 5 个内置工具逐个列成一行，而每行点开的是**同一个**
编辑器——只增加高度、不增加信息。改成按来源各一行（内置工具 / MCP 服务 / Python 算子）并给出
数量，工具名留在该行 `title` 里；「高级设置」不再写死「运行时、权限与沙箱」，改显示草稿的
`runtime`。截图确认：面板从 6 行工具变成 3 行来源，高级设置显示 `deepagents`。

**#6 动效统一**：侧栏（版本历史、配置编辑器、MCP）共用一个 `studio-rail-in` 进场；
展开的分组用 `studio-group-in` 轻微揭示；会话菜单浮层用 `studio-popover-in` 从锚点缩放进入。
三处都在 `prefers-reduced-motion: reduce` 下关闭。配置编辑器用 `hidden` 属性切换，所以动画挂在
`:not([hidden])` 上才能每次打开都重放（挂在 `data-open` 上等于永不播放）。

**#7 Skill 勾选**：智能体的 Skills 区新增「平台技能」清单，直接读
`GET /v1/studio/skills/catalog`，勾选即按**精确包版本**安装（`installPlatformSkill`，
与其它安装走同一 revision 守卫），取消勾选走既有卸载流程，`riskLevel === "review"` 的包在行上
标注「需审阅」。截图确认：21 项可用、每项一个复选框。

**评审 P1（模板 domain）**：`applyAgentTemplate` 原来写 `domain: template.id`，于是
`pr-reviewer` 这类 slug 被持久化成草稿的领域，而 `domain` 会参与生成该草稿自己的评测 prompt
（`agent-studio-workbench.tsx:234`）。改为只更新身份字段，并加测试固定 domain 不被覆盖。

**评审 P1（发布门禁）**：快照/包哈希判据在 `service.py` 手写 6 处，而它的**正路径此前无测试**。
没有测试就拍平 6 处比较等于把门禁交给运气，所以先补测试
`test_publication_guard_compares_the_whole_release_identity`：固定「同 manifest、不同 package
不得被当作基线」这一性质（正是 6 处必须共同守住的不变量），覆盖此前缺的正路径。

测试：vitest 129 文件 / 815 passed；`pytest tests/unit` 1476 passed；
新增 `tests/agenta-configuration.spec.tsx` 用 jsdom 渲染面板，把 #5 的行结构钉住（比截图更硬）。

## 7. 第四批：追加 4 项（同日）

| 项 | 内容 |
| --- | --- |
| 版本 | `4818dc29`（item 1 + item 2/4 的 MCP 路径） |
| 镜像 | 只换 web：`kai/axis-web:evolution-4818dc29`（api/worker 仍 `evolution-d6bada40`） |
| 回滚点 | `backups/4818dc29/compose.json` |

**item 1 卡片样式**：`.agentCardAction` 是固定 30×30 的**圆形图标盒**，里面却装着文字
「查看工作区 →」，叠加 `white-space: nowrap` 就把标签画到卡片外面（用户图 1 的红框）。
改成文字胶囊（inline-flex + padding + 999px 圆角），并删掉窄屏处重复的同一句声明。
实测：宽 **30px（溢出）→ 94px 且完全在卡片内**，截图确认页脚左版本号/右入口对齐。

**item 2 MCP 行 + 直达表单**：配置列表新增「MCP 服务器」行（显示已启用数量 + 自己的 `＋`）。
关键是那个组件的表单**本身就是右侧抽屉**（fixed / 680px / z-70），我原先把它整个塞进一个 rail，
于是变成「抽屉里再叠抽屉」，还得先过一层卡片才能点到字段——正是用户说的多一次点击。
现在 `＋` 直接以 `startInForm` 打开它自己的抽屉，截图确认一次点击就到
「注册 MCP · 01 基本信息 / 02 连接配置 / 03 鉴权」的全部字段；不带 `＋` 打开则看目录。
同时删掉 Tools 区里重复的「管理 MCP 服务器」入口。

**未做（下一步）**：item 3「文件与知识可勾选绑定自己有权限的知识库」，以及 item 4 剩下的那一半——
`文件与知识` 行目前仍指向 `capabilities`（工具）分区，应新增 `knowledge` 分区，
用 `studioClient.listKnowledgeBases()` 的勾选列表绑定 `draft.knowledgeReferences`，
让每一行抽屉都直达自己的配置项。

## 8. 第五批：MCP 注册表单瘦身（同日）

| 项 | 内容 |
| --- | --- |
| 版本 | `d76ab875` |
| 镜像 | web `kai/axis-web:evolution-d76ab875`（api/worker 仍 `evolution-d6bada40`） |
| 回滚点 | `backups/d76ab875/compose.json` |

用户反馈「MCP 的配置项太多了」。原来三步共 11 个字段，其中两格说的是同一件事：
`引用标识` 已经决定工具名前缀（`save()` 里本就有 `const serverName = draft.serverName?.trim() || reference;`），
所以独立的「MCP 服务名」是冗余的。做法：

- 删掉「MCP 服务名」字段（沿用既有派生，`save()` 的兜底断言进测试）；
- 「能力说明」从 3 行改 1 行；
- 治理类项「传输类型读数 / 自定义请求头 / 风险级别 / 网络范围 / 执行位置」保留默认值，
  整体收进 `高级设置（可选）` 折叠块。

现在注册只需 **引用标识 / 显示名称 / 能力说明 / MCP 地址 / 鉴权方式** 五项，截图确认
（02 里只剩 MCP 地址 + 折叠的高级设置）。测试两头都钉住：折叠块存在，且那些治理项确实在其内部
（`tests/mcp-catalog-control-plane.spec.ts`）。vitest 129 文件 / 818 passed。

## 9. 仍未做

- 状态字面量收敛成 StrEnum（评审 P1-4）、`run-status.ts` 标签的多处自建（P1-7）、
  `agent-builder-overlays.tsx` 重复的意图正则（P1-3）等；
- 上面 6 处哈希判据的**收敛重构**（测试已就位，可以安全做）；
- 状态码/文案类打磨（评审 P2：布尔当计数、`budget_exhausted` 标签、`generation_attempts` 死字段）。


- **#5** 参考 agenta 简化智能体配置下方「工具 / 高级设置」抽屉；
- **#6** 抽屉等整体动效优化；
- **#7** 智能体配置的 Skill 支持勾选（后端已有 `GET /v1/studio/skills/catalog` 与
  `PUT /drafts/{id}/skills/references`，可直接支撑勾选式引用）。

评审（`docs/agent-evolution-code-review-20260922.md`）里的 P0-1（`_save` 走
`model_copy` 绕过校验）、P0-2（5 处 CAS 只判 revision）、P1 系列也仍待处理。
