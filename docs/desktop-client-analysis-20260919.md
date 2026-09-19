# KAI Agent Studio 桌面形态分析（2026-09-19）

参照系：DeepSeek Harness 官方桌面端（`deepseek-ai/deepseek-harness` → `apps/desktop`）与社区
dsh-desktop 发行版（anywhere-labs / vibeinging / huyang218 / dataelement / yxccai）。

本文只做分析与方案设计，不含实现。所有现状结论都标注了仓库内证据路径。

---

## 0. 结论摘要

1. **「做成 desktop」对本项目不是打包问题，而是形态选择问题。**
   DSH 之所以能一周做出官方桌面端，是因为它本来就是**单用户本地 Web 工具**：一个 Node 进程 +
   一个浏览器界面。本项目是**多租户控制面**（Postgres/Redis/MinIO/Worker/审批/配额/审计/
   K8s 沙箱），把浏览器换成窗口并不会让它变成桌面应用。

2. **推荐形态：双模客户端（Option C）。**
   同一套渲染层，两种后端归属：
   - `remote` 模式：桌面客户端 = 174/K8s 控制面的原生客户端（替换浏览器）；
   - `local` 模式：桌面客户端 = 自带本地 API/Worker 的单机版（离线可用）。
   二者共用 UI 与协议，差别只在**组合根（composition root）**与凭据来源。

3. **渲染层应选 `web/kai-webui`，不是 `web/harness-console`。**
   kai-webui 是 Vite 纯静态 SPA（`src` 仅 7 个 TS 文件 / 104K，`dist` 224K，依赖只有
   react / react-dom / lucide-react），且**本身就是从 DeepSeek Harness UI 派生**的，
   直接说 Harness 协议。harness-console 是 Next.js + 约 30 个 route handler 组成的 BFF
   （`web/harness-console/src/app/api/**/route.ts`），要跑 `node server.js` 才能用，
   装进桌面等于在应用里再养一个 Node 服务端。

4. **外壳应选 Electron，不是 Tauri** —— 但只在选了 `local` 模式时结论才这么硬。
   若只做 `remote` 模式的瘦客户端，Tauri 反而更优（见 §5.2）。

5. **最大的真实成本在「本地运行时闭包」和「存储替换」，不在窗口壳。**
   仓库当前 **`src/` 下没有任何 SQLite 痕迹**（`grep -ri sqlite src/` 零命中），
   组合根只有 `build_production_container`（`src/harness/composition.py:547`），
   队列只有 Redis 实现，制品只有 MinIO 实现。这是 `local` 模式的全部工作量所在。

6. **必须守住一条平台不变量**：不支持的能力在**编译期/校验期**拒绝，不得静默降级。
   `local` 模式下 `SandboxEnforcement` 只能是 `none`（`src/harness/sandbox/base.py`
   已如实建模：local/workspace → `NONE`，kubernetes/gvisor → `FULL`，
   daytona/e2b/cubesandbox/opensandbox → `DELEGATED`）。因此引用 gVisor/Daytona 的
   Execution Profile 必须在桌面本地模式下**拒绝运行**，而不是退回本地进程跑。

---

## 1. 现状盘点（仓库事实）

### 1.1 后端：一个控制面，三个进程角色，四个基础设施依赖

| 角色 | 入口 | 说明 |
| --- | --- | --- |
| API | `harness.cli:entrypoint` / `deploy/docker/entrypoint-api.sh` | FastAPI，`:8000`，含 `/mcp/memory`、`/mcp/knowledge`、`/mcp/platform` 三个内嵌 MCP app（`src/harness/api/app.py:289-291`） |
| Worker | `harness.worker.main:entrypoint` | 轮询队列执行 Run，是真正跑 Claude SDK / Codex / DeepAgents 的进程 |
| Quality Worker | `harness.quality.main:entrypoint` | 质量门禁，compose 里挂在 `observability` profile 下 |

基础设施依赖（`deploy/docker-compose/compose.yaml`）：

```
postgres → redis → minio → minio-init → migrate → api / worker / quality-sync / seed → web → otel-collector
```

配置面（`src/harness/config.py`）：`database_url`（postgres+asyncpg）、`redis_url`、
`minio_*`、`environment ∈ {local,test,production}`、`runtime ∈ {fake,claude-sdk,multi}`、
`sandbox_provider ∈ {local,daytona,e2b,kubernetes,cubesandbox,opensandbox}`。

**端口抽象是齐的**（`src/harness/core/ports.py`）：`TaskQueue`（enqueue/dequeue/ack/retry/
extend_lease）、`ArtifactStore`（put/get/delete）、`AgentRegistry`、各 Repository。
这是 `local` 模式唯一的好消息 —— 替换的是适配器，不是业务逻辑。

**但实现只有一个**：全仓没有 SQLite，没有内存/进程内队列的生产实现，
`build_production_container` 是唯一组合根。

### 1.2 运行时闭包（API 镜像里已经塞了什么）

`deploy/docker/api.Dockerfile` 的事实：

| 组件 | 版本/来源 | 桌面影响 |
| --- | --- | --- |
| Python | `python:3.12-slim-bookworm` | 需自带解释器或冻结 |
| Codex CLI | `0.149.0`，**仅 `linux-x64` musl** | macOS arm64 / Windows 无产物 |
| Codex sandbox helper | `codex-linux-sandbox` 符号链接 | Linux 专属机制，macOS 走 Seatbelt，路径不同 |
| Node.js | `22.9.0` + 全局 `docx` / `pptxgenjs` | 平台技能出 .docx/.pptx 依赖它 |
| kubectl | Chainguard 镜像（Cosign 校验） | 桌面不需要 |
| libarchive13 | RAR4/RAR5 读取 | 需平台对应动态库 |
| Claude CLI | `claude_agent_sdk/_bundled/claude`（`src/harness/sandbox/claude_cli.py:bundled_cli_path`） | 平台相关二进制，桌面需确认 wheel 是否覆盖 darwin/win32 |
| DeepAgents 内核 | `--extra deepagents`（langchain 系） | 可选下载，不进默认闭包 |

### 1.3 前端：四个应用，两种形态

| 应用 | 栈 | 规模 | 是否需要 Node 服务端 | 桌面适配性 |
| --- | --- | --- | --- | --- |
| `web/kai-webui` | Vite + React（3 个依赖） | 7 个 TS 文件 / `dist` 224K | **否**（静态 + nginx 反代 `/api/`） | ★★★ 理想渲染层 |
| `web/harness-console` | Next.js App Router + assistant-ui + three.js + antv/g6 + mermaid + codemirror + exceljs | 218 个 TS 文件 / `src` 3.7M | **是**（约 30 个 route handler 做 BFF 与 cookie 鉴权，`node server.js`） | ★★ 需内嵌 Node |
| `web/codex-web` | Next.js + 自建 bridge | — | 是，但已打成 CLI（`build:cli` → `dist/cli/codex-web.mjs`，esbuild/node20.9） | ★★ 已有「本地服务 + 前端」打包先例 |
| `web/codex-webui` | NestJS(Fastify) + React + node-pty + better-sqlite3 + drizzle + `@openai/codex` | — | 是（本地 SQLite、PTY、文件、预览、JWT） | ★★★ 已经是桌面形状 |

**这张表里最重要的一行是 `kai-webui`**：它是从 DSH UI 派生的窄界面（README 明说
"intentionally excludes terminal and local-workspace features"），协议是 Harness 原生
（AG-UI 只是可替换的流适配器）。桌面渲染层用它，等于**天然对齐了参照系的架构**。

`codex-webui` 则证明仓库里已经存在「本地服务 + 本地 SQLite + PTY」的完整工程实践 ——
`local` 模式不必从零发明。

---

## 2. 参照系拆解：DSH desktop 到底做了什么

### 2.1 官方 `apps/desktop`

自述定位：**"an Electron shell around the complete dsh Web application"** —— 薄外壳。

| 维度 | 官方做法 |
| --- | --- |
| 框架 | Electron（**非 Tauri**），electron-builder + tsdown + TypeScript |
| 渲染层 | 打包后的 Web 入口，走自定义协议 `dsh-app://app/` |
| 启动链 | Electron RunAsNode 子进程启动共享 profile runner → 主进程加载渲染层；Node IPC 负责 boot injection / readiness / shutdown |
| 服务进程 | dsh 以 `ELECTRON_RUN_AS_NODE=1 --expose-internals` 运行，**不要求系统装 Node/pnpm** |
| 运行时闭包 | 内置独立 Node.js、Python、pnpm 发行版 |
| 端口 | 桌面 `19387`（Web 端 `3080`） |
| 网络模型 | Electron 转发 HTTP 到**已认证的 Web Host**；WebSocket 流携带凭证连接该 Host |
| 原生模块 | `node-addon-require-builtin` 用精确 V8 构建串做准入校验（fail closed）；`fs-ext` 按实际 Electron 版本动态重建；node-pty/koffi 是 N-API 不受 ABI 影响 |
| 打包 | Windows NSIS、macOS universal（arm64+x64 合并）、Electron fuses 校验 |
| 更新 | 普通更新 + 强制更新两条流；`primary-runtime-lock.json` 锁运行时载荷；nightly 通道 |
| 版本管理 | stable/beta 双通道，上游 `deepseek-harness` 作 git submodule，`upstream.json` 记录 pin |
| 平台坑 | Windows 隐藏控制台（`dwFlags:257` / `wShowWindow:0`）；`backgroundThrottling` 导致渲染进程内存不回收，改为最小化时显式重绘 |

### 2.2 社区版（vibeinging / anywhere-labs / 等）

理念是 **"Electron 只是一层很薄的桌面 Host"**：

- 桌面只负责：窗口、原生文件选择、更新、启动与恢复；Agent/会话/插件仍归 DSH 本体。
- **安装目录与数据目录分离**：客户端装应用，`~/.dsh`（可用 `DSH_DATA_ROOT` 覆盖）存 Profile/
  插件/Session，换安装位置不丢数据。
- **恢复页**：Profile 或客户端出错时可重试、安全启动、移除问题插件、导出诊断；
  插件启动失败**不静默改写原 Profile**。
- 官方 Profile 是唯一权威，市场/设置/CLI 操作同一份数据。
- 预置 16 个 Bundle（Better Sidebar、插件市场、任务看板、Git Worktree、手机远程、会话团队…），
  用「插件」而不是「fork」扩展桌面能力。
- 平台：macOS arm64 + Windows x64 有正式包；macOS Intel / Linux 只能源码跑。

### 2.3 可以照搬的四条经验

1. **外壳薄、能力走插件**。桌面专属能力（原生文件对话框、托盘、更新）通过窄适配层提供，
   不复制一份 Chat/Agent 逻辑。
2. **安装目录与数据目录分离**，数据目录可用环境变量重定位。
3. **运行时闭包自带，且带清单校验**（`desktop-runtime.json` 绑定 shell 版本、Electron 的
   Node 版本、平台架构），启动时校验，不一致就 fail closed。
4. **更新分两条轴**：外壳（Electron app）与运行时载荷（CLI/解释器）分开更新，
   否则每次 CLI 小版本都要重装整个应用。

### 2.4 不能照搬的地方（关键差异）

| 维度 | DSH | 本项目 |
| --- | --- | --- |
| 用户模型 | 单用户、本地、无租户 | 多租户 + RBAC + 团队空间（`docs/team-spaces.md`） |
| 持久化 | 本地文件/SQLite 即可 | Postgres + Redis + MinIO，且记忆 v2 用 pgvector |
| 执行位置 | 本机进程 | 沙箱（Daytona/E2B/CubeSandbox/gVisor），远程为主 |
| 治理 | 无 | 审批、配额、审计账本、晋级回滚、质量门禁 |
| 控制面唯一性 | 无此约束 | **明确不变量**：平台是唯一控制面，不得出现第二套暂停恢复机制或第二份事件流 |
| 客户端数量 | 1 | 浏览器控制台 + 桌面 + Codex workbench + A2A/Webhook 外部接入 |

**结论：DSH 的桌面端是「把本地工具装进窗口」；本项目的桌面端必须先回答「这个窗口连谁」。**

---

## 3. 三种桌面形态

### Option A — 瘦客户端（连远程控制面）

桌面 = 原生窗口 + 系统集成，后端仍是 174/K8s。

- **包含**：原生文件选择与拖放、系统通知、托盘常驻、深链接（`kai://run/...`）、
  本地文件夹作为工作区、OS 钥匙串存 token、离线时展示缓存视图。
- **不包含**：任何本地执行、任何本地数据库。
- **优点**：工作量最小；**完全符合「平台是唯一控制面」不变量**；企业部署友好；
  权限/配额/审计天然统一。
- **缺点**：断网即不可用；"桌面"的增量价值主要是文件与系统集成。
- **代价**：S 级。主要是外壳 + 原生适配层 + 设备流登录。

### Option B — 全本地单机版（把整条执行链装进本地）

桌面 = 自带 API/Worker/存储/沙箱的单机应用。

- **包含**：本地 SQLite 存储、进程内队列、文件系统制品库、`LocalSandboxProvider`
  （`src/harness/sandbox/local.py`，已是 `mkdtemp` + 隔离环境变量的子进程执行）、
  单用户免登录、内置 Python/Node/Claude CLI/Codex 闭包。
- **优点**：离线可用、隐私好、演示与个人版体验最佳。
- **缺点**：运行时闭包巨大（见 §6）；Postgres 语义（JSONB、`func`、pgvector、Alembic）
  要另起一套；沙箱隔离降级为 `NONE`，必须如实声明；与云端能力（记忆 v2 语义召回、
  知识库、晋级/审计）割裂。
- **代价**：XL 级。其中「存储替换」一项就 ≥ 一个独立阶段。

### Option C — 双模客户端（推荐）

同一渲染层 + 两个组合根：

```
                    ┌─────────────────────────────┐
                    │  Electron 主进程（薄外壳）    │
                    │  窗口/托盘/更新/文件/钥匙串   │
                    └──────────┬──────────────────┘
                               │ IPC + 自定义协议
                    ┌──────────▼──────────────────┐
                    │  渲染层：kai-webui 静态包     │
                    │  （Harness 协议 / AG-UI 适配） │
                    └──────────┬──────────────────┘
                               │ HTTP + WS（每次启动随机 token）
        ┌──────────────────────┴──────────────────────┐
        │                                             │
┌───────▼────────┐                          ┌─────────▼─────────┐
│ remote 模式     │                          │ local 模式         │
│ 连 174 / K8s    │                          │ 自带子进程          │
│ OAuth 设备流    │                          │ 单用户本地 token    │
│ 平台凭据        │                          │ 本地组合根          │
└────────────────┘                          └───────────────────┘
```

- 一套 UI、一套协议、一套事件模型；差别只在「组合根 + 凭据来源」。
- `remote` 先落地（S 级），`local` 作为可选能力逐步补齐（L~XL 级），
  用户拿到的是同一个应用。
- 与仓库现有结构天然契合：`build_production_container` 之外再加
  `build_local_container`，而不是 fork 一套代码。

**推荐理由**：它把「桌面」拆成两个可独立交付的价值，先拿到 80% 收益（原生体验 + 文件集成 +
系统通知），再把高风险高成本的部分（存储替换、运行时闭包）做成可选下载，
而不是一开始就背上 500MB+ 的闭包。

---

## 4. 逐层改造清单

### 4.1 渲染层

| 项 | 现状 | 桌面需要 |
| --- | --- | --- |
| 静态化 | kai-webui 已是静态 `dist` | 直接可用；自定义协议 `kai-app://app/` 提供，避免 `file://` 的 CORS/同源问题 |
| API 基址 | nginx 反代 `/api/` | 主进程注入实际端口；禁止把 token 放 query string |
| 登录 | 浏览器 OAuth 重定向（`web/harness-console/src/app/api/auth/**`） | **设备码流程**；桌面二进制里的 client secret 可被提取，不能内嵌 |
| 构建/Studio 界面 | 只在 harness-console（three.js/g6/mermaid/codemirror） | **需决策**：桌面是否要构建工作台？若要，需把 Studio 页面迁入 kai-webui 或内嵌 harness-console |
| 终端/本地工作区 | kai-webui 刻意排除 | 若做 local 模式，可借鉴 `web/codex-webui/src/terminal`（node-pty）但应放在**普通 Node 子进程**里，不放 Electron 主进程 |

### 4.2 外壳与进程模型

| 项 | 建议 |
| --- | --- |
| 框架 | Electron（对齐 DSH）。理由见 §5.2 |
| 主进程职责 | 单实例锁、端口分配（建议 `bind :0` 取实际端口，不要像 DSH 硬编码 19387）、子进程监管与退避重启、就绪门控（API `/healthz` 通过后才显示窗口）、退出时收割孤儿进程 |
| 启动注入 | 参照 DSH 的 Node IPC：把随机 bearer token、端口、数据目录经 IPC/env 传给子进程；渲染层经 preload `contextBridge` 取，**不落 URL、不落磁盘** |
| Worker 归属 | `local` 模式建议**进程内**（asyncio task）而非第二个子进程，少一套闭包与生命周期；`remote` 模式保持服务端子进程不变 |
| 原生模块 | **不要在 Electron 里放原生模块**。PTY、文件监视等放普通 Node 子进程（`codex-webui` 已验证这条路），避免重演 DSH 的 ABI 校验与 `fs-ext` 重建问题 |
| 数据目录 | 安装目录与数据目录分离，`KAI_DATA_ROOT` 可覆盖（对齐 DSH 的 `DSH_DATA_ROOT`） |
| 平台坑 | Windows 隐藏控制台窗口；macOS 最小化时的渲染内存回收；两平台都要过一遍 |

### 4.3 存储替换矩阵（`local` 模式的核心工作量）

| 关注点 | 服务端 | 本地替代 | 端口是否已存在 | 难度 |
| --- | --- | --- | --- | --- |
| 关系库 | PostgreSQL + asyncpg | SQLite + aiosqlite | 是（各 Repository） | **高**：类型/JSONB/`func`/锁语义差异；Alembic 需第二条迁移线 |
| 向量记忆 v2 | pgvector | `sqlite-vec` 或进程内暴力检索 | 是 | **中高**：语义召回是已发布能力，不能悄悄关掉 |
| 任务队列 | Redis（含 lease/visibility/retry） | 进程内队列 + SQLite 表做持久化 | 是（`TaskQueue`） | **中**：lease/重试/可见性语义必须等价，否则 Run 恢复语义变了 |
| 事件发布订阅 | Redis | 进程内广播 | 是 | 中 |
| 制品库 | MinIO/S3 | 本地目录 + sha256 | 是（`ArtifactStore`） | 低 |
| 密钥 | 环境变量 / MCP 凭据库 | 系统钥匙串（Keychain / DPAPI / libsecret） | — | 中 |
| 记忆/知识 MCP | 远程 workload（`/mcp/memory`、`/mcp/knowledge`） | 关闭或本地 | — | 低（能力开关） |
| 沙箱 | 6 个 provider | 仅 `local` | 是 | 功能上低，**但隔离等级必须如实降级为 `NONE`** |

**工程建议**：不要试图让 SQLite 兼容 Postgres 方言。更稳的做法是
**把「本地存储」定义为一个独立的持久化契约实现**（SQLAlchemy 侧换 dialect + 独立的
Alembic 分支 + 明确声明本地模式不支持的能力集合），而不是让两套 DDL 强行对齐。

### 4.4 运行时闭包清单（`local` 模式的硬成本）

必须随包或按需下载的：Python 3.12 运行时、Node 22（office 技能）、Claude CLI、
Codex CLI、libarchive、以及平台技能依赖的 npm 包（`docx`、`pptxgenjs`）。

必须新建的产物（当前镜像里**不存在**）：

- `codex` 的 **macOS arm64 / x64** 与 **Windows x64** 二进制；`codex-linux-sandbox`
  这套 helper 是 Linux 专属，macOS 走 Seatbelt，需要单独验证。
- `claude-agent-sdk` 的 `_bundled/claude` 是否随 wheel 覆盖 darwin/win32 —— **待验证项**，
  若不覆盖则需单独分发。
- 每个平台的 libarchive 动态库。

必须配套的机制（直接抄 DSH 的作业）：

- **运行时清单**（对齐 `desktop-runtime.json`）：绑定外壳版本 ↔ Python/Node/CLI/Codex 版本
  ↔ 平台架构，启动校验，不一致 fail closed。
- **闭包校验脚本**：把「声明的依赖」和「实际装载的依赖」做集合比对，防止静默漂移。
- **外壳与运行时分离更新**，用 lock 文件锁住运行时载荷版本。

体积量级：Electron 基线约 200MB 级，Python 闭包（含 cryptography/pillow/mcp/SDK）数百 MB，
Node + office npm 包数十 MB，两个 CLI 各约百 MB 级 —— **总体在数百 MB 到 1GB 量级**，
且需按平台各自出包。这是 `local` 模式必须正视的现实。

### 4.5 认证与租户

| 场景 | 做法 |
| --- | --- |
| `remote` | OAuth **设备码流程**（不要内嵌 client secret）；refresh token 进系统钥匙串；复用现有 JWT/租户模型 |
| `local` | 单用户；`auth_default_tenant_id` 默认已是 `"local"`（`src/harness/config.py`）。本地 API **只绑 `127.0.0.1`** + **每次启动随机 bearer token**，否则同机任意进程/浏览器都能打本地 API —— 这是 Electron 本地服务的经典漏洞，DSH 用「已认证 Web Host + 携带凭据的 WebSocket」解决 |
| 治理一致性 | 本地模式的审批 UI 必须保留（审批是产品语义，不是基础设施）；审计写本地库并支持导出/回传 |

### 4.6 更新与发布

- 外壳：`electron-updater` + 稳定/预览双通道（对齐 DSH 的普通/强制更新两条流）。
- 运行时：独立载荷 + lock 文件，支持「只更新 CLI」而不重装应用。
- 客户端需声明协议版本，服务端需支持 N-1 客户端；仓库已有 `versioning.py`、
  `release*.py` 与签名 manifest 体系可复用。
- 签名：macOS Developer ID + 公证（需 JIT 相关 entitlements 给 Electron）；
  Windows Authenticode；Linux 暂缓（DSH 社区版也没做 Linux 正式包）。

### 4.7 沙箱与治理（不变量）

`src/harness/sandbox/base.py` 已经把隔离等级建模成事实：

```
kubernetes / gvisor        → FULL
daytona / e2b / cubesandbox → DELEGATED
opensandbox                 → DELEGATED
local / workspace           → NONE
```

因此桌面 `local` 模式下必须：

1. 引用 `FULL`/`DELEGATED` 后端的 Execution Profile **在校验期拒绝**，不回退本地执行；
2. Run 事件里如实记录 `enforcement: none`，与 `sandbox_trust_floor_mode` 语义一致；
3. 保留 `allow_unsafe_local_sandbox` 这类显式开关的语义 —— 危险路径要显式打开，不默认开。

---

## 5. 关键技术选型论证

### 5.1 渲染层：kai-webui 还是 harness-console

| 判据 | kai-webui | harness-console |
| --- | --- | --- |
| 能否静态托管 | 能（`dist` 224K） | 不能，依赖 route handler |
| 桌面内额外进程 | 无 | 一个 Next.js `server.js` |
| 协议契合 | Harness 原生，AG-UI 可替换 | 经 BFF 转发 |
| 功能覆盖 | 窄（刻意排除终端/本地工作区） | 全（Studio/构建/管理台） |
| 与 DSH 的关系 | 派生自 DSH UI | 自研 |

**结论**：kai-webui 作桌面渲染层。但要正视它的窄：**如果桌面要承载「构建/Studio」，
就必须先决定是把 Studio 迁进去，还是桌面只做「任务与运行」而构建仍在浏览器里做。**

### 5.2 Electron 还是 Tauri

| 判据 | Electron | Tauri |
| --- | --- | --- |
| 壳体积 | ~200MB 级 | ~10MB 级 |
| 渲染一致性 | 自带 Chromium，跨平台一致 | 系统 WebView（WKWebView / WebView2），差异风险 |
| 子进程/PTY/原生模块 | 生态成熟，DSH 已验证 | 需 Rust 侧自己写 |
| 与「本地 Python 闭包」共存 | 自然（就是多进程监管） | 也可以，但省下的壳体积被 Python 闭包淹没 |
| 参照系一致性 | 与 DSH 官方/社区一致 | 无先例 |

**结论**：
- 选 `local` 模式 → **Electron**。硬成本在 Python 闭包与原生模块 ABI，壳体积不是主要矛盾，
  而且 DSH 踩过的坑（ABI 校验、fuses、backgroundThrottling、Windows 控制台）都已有公开答案。
- 只做 `remote` 瘦客户端 → **Tauri 值得认真考虑**：不需要 Node 运行时、不需要 PTY、
  不需要原生模块，10MB 壳 + 系统 WebView 是更干净的形态。

即：**外壳选型依赖形态选型**，不要先定框架。

---

## 6. 风险与不变量

**必须守住的平台不变量**

1. 平台仍是唯一控制面：Run 状态机、审批、策略、配额、事件日志、工作区归档、制品发布
   的所有权不变。桌面端**不得**引入第二套暂停恢复机制或第二份事件流。
2. 不支持的能力在**校验期**拒绝，不静默降级（本地模式的沙箱等级是第一个适用对象）。
3. Harness 事件是权威事实，AG-UI 只是无状态投影 —— 桌面端也必须走这条路径，
   不能因为「在本地」就绕开事件模型。

**主要风险**

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 存储双线维护 | SQLite 与 Postgres 两套 DDL/迁移长期漂移 | 独立迁移分支 + 契约测试覆盖两侧；明确声明本地模式不支持的能力集合 |
| 运行时闭包漂移 | CLI/解释器版本与平台技能不一致 | 运行时清单 + 闭包校验脚本 + fail closed |
| 原生模块 ABI | 一旦把原生模块放进 Electron 主进程就会重演 DSH 的 ABI 地狱 | 原生模块一律放普通 Node 子进程 |
| 本地 API 暴露 | 同机其他进程可访问本地服务 | 仅绑 127.0.0.1 + 每次启动随机 token + 系统钥匙串 |
| 能力割裂 | 本地模式没有记忆 v2 / 知识库 / 晋级审计 | 明确标注为「本地模式不可用」而非静默关闭；需要时提示连远程 |
| 三套前端分裂 | kai-webui / harness-console / codex-web(ui) 已四份 | 桌面只认一份渲染层；其余明确归属（企业控制台 / Codex 工作台） |
| 平台包缺失 | Codex 与 Claude CLI 的 macOS/Windows 产物当前不存在 | 作为独立工作项提前验证，不要留到最后 |

---

## 7. 分阶段落地建议

| 阶段 | 内容 | 规模 | 依赖 |
| --- | --- | --- | --- |
| P0 形态定案 | 定 `remote` / `local` / 双模；定桌面是否承载 Studio | S | 需产品决策 |
| P1 瘦客户端 | Electron 壳 + kai-webui 静态包 + 自定义协议 + 设备流登录 + 钥匙串 + 托盘/通知/深链接 | S | P0 |
| P2 桌面增强 | 本地文件夹作为工作区、原生文件对话框与拖放、断网缓存视图、协议版本协商 | M | P1 |
| P3 本地组合根 | `build_local_container`：SQLite + 进程内队列 + 文件制品库 + 本地沙箱 + 单用户模式；校验期拒绝非本地后端 | L~XL | P0 |
| P4 运行时闭包 | Python/Node/Claude CLI/Codex 各平台产物；运行时清单 + 闭包校验 + 外壳/运行时分离更新 | L | P3 |
| P5 能力补齐 | 本地记忆检索（sqlite-vec 或本地暴力检索）、知识库取舍、审计导出 | M | P3 |

**建议路径**：P1 先做，它独立、低风险、立刻有产品价值，且不触碰任何平台不变量。
P3/P4 作为 `local` 模式的独立投资，在 P1 验证了桌面形态价值之后再启动。

---

## 8. 待决策问题

1. **桌面要连谁？** 远程控制面的客户端（A/C-remote），还是单机离线版（B/C-local），还是两者都要？
2. **桌面要不要承载「构建/Studio」？** 若不要，kai-webui 直接够用；若要，需先把 Studio
   页面迁入 kai-webui，或接受内嵌一个 Next.js 服务端。
3. **目标平台优先级？** DSH 官方是 macOS + Windows；社区版 macOS arm64 + Windows x64。
   macOS Intel / Linux 是否在范围内，直接决定运行时闭包要出几份。
4. **`codex-webui` 与桌面的关系？** 它已经是「本地服务 + SQLite + PTY」的形状，
   是并入桌面作为一个能力面板，还是保持独立产品？
5. **本地模式的能力边界如何对外表述？** 记忆 v2、知识库、晋级审计在本地不可用时，
   是硬性标注「不可用」，还是提供降级实现？

---

## 附：证据索引

- 组合根与端口：`src/harness/composition.py:547`、`src/harness/core/ports.py`
- 沙箱等级：`src/harness/sandbox/base.py`、`src/harness/sandbox/local.py`
- Claude CLI 单源：`src/harness/sandbox/claude_cli.py`
- 配置面：`src/harness/config.py`
- 服务拓扑：`deploy/docker-compose/compose.yaml`
- API 镜像闭包：`deploy/docker/api.Dockerfile`
- Web 镜像与 Next.js standalone：`deploy/docker/web.Dockerfile`
- 渲染层：`web/kai-webui/README.md`、`web/harness-console/src/app/**/route.ts`、
  `web/codex-web/scripts/build-cli.ts`、`web/codex-webui/src/**`
- 参照系：`deepseek-ai/deepseek-harness` → `apps/desktop`；
  `anywhere-labs/dsh-desktop`、`vibeinging/dsh-desktop`
