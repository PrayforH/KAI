# 图谱造型回退 + 定位保持 / 点击消歧 · 173 发布记录

- 日期：2026-09-10
- 分支：`feature/weknora-knowledge-base`
- 提交：`0788bf8`（回退造型 + 定位保持）、`5925f02`（点击消歧）、`f9c3068`（交互测试）
- 目标环境：`172.20.109.173`（API `:8800`，Web `:3301`）
- 发布 tag：`graph-rollback-20260910`（web `sha256:3d8c8f5b`，api 与上一版同 digest `sha256:59bdecf8`）
- 上一版 web tag：`knowledge-graph-3d`
- env 备份：`.env.production.bak-graph-rollback-20260910-095736`

## 1. 本轮解决的三个问题

| # | 现象 | 归属 |
| --- | --- | --- |
| 1 | 3D 节点多面体造型（二十面体/八面体/十二面体/立方体 + 线框外壳）观感不佳，需要回退 | 视觉回退 |
| 2 | 从 Wiki 页「在图谱中查看」跳转：已经定位到目标节点，最后又被拉回整图（2D、3D 都有） | 交互缺陷 |
| 3 | 2D 双击展开邻居与单击开抽屉冲突，多数双击最后留下打开的抽屉 | 交互缺陷 |

## 2. 根因

**(1) 造型**：多面体按类型区分剪影的设计在实际图谱密度下辨识收益有限，线框外壳在辉光叠加下发灰、削弱了原本的通透感。回退为统一球体 `SphereGeometry(radius, 24, 24)` + `MeshBasicMaterial`，悬停压暗也从"多材质循环"回到单材质。

**(2)(3) 两个缺陷同源**：都是"自动取景/延时动作在用户的显式意图之后落帧"。具体到代码：

| 缺陷 | 机制 |
| --- | --- |
| 2D 定位被拉回 | `instance.fitView({when:"always"})` 与 `focusElement(..., 380ms)` 在同一 tick 内起动画。G6 的 `DEFAULT_ANIMATION_OPTIONS` 是 **500ms**，fit 比 focus 晚 120ms 结束，于是 fit 写下最后一帧——整图。 |
| 3D 定位被拉回 | 定位飞行 700ms 之后，`onEngineStop` 与 1.4s/3.6s 兜底定时器仍会 `zoomToFit` 整图；力导向在这期间还在移动节点，落点也早已偏离。 |
| 2D 双击变抽屉 | 单击开抽屉走 240ms 延时定时器，而双击窗口由浏览器原生 dblclick 决定（macOS 默认约 500ms）。只要两次点击间隔 >240ms，抽屉先弹出，expand 随后发生在抽屉底下。 |

三次改动之前，`git checkout cd2a121 -- knowledge-graph-panel.tsx` 后新交互测试 4/6 失败，其中就包含「双击留下抽屉」与「跳转定位被 fitView 覆盖」。

## 3. 修复

- **定位保持**：新增 `focusHoldRef`（相机锚定本次跳转的目标 slug）。锚定期间一切自动全图取景让位；2D 带目标时**跳过首次 fit**，3D 的兜底 `autoFit` 增加锚定判断，且 `onEngineStop` 改为复核目标节点的**当前**坐标重新对准（力导向期间节点仍在移动）。用户滚轮/旋转或点「适应屏幕」即解除锚定。同一 slug 每次跳转只对准一次，避免数据同步把用户手动平移的视口拽回。
- **点击消歧**：两个引擎共用一条点击判定 `resolveNodeClick`（第二次点击落在 `DBLCLICK_MS=400` 内即认定为展开，并立即取消待开抽屉）；由于慢双击可能在延时定时器触发之后才到，额外用 `openedByClickRef` 记录"这次延时开的是哪个节点"，`undoClickOpen` 把抽屉收回（只收回自己刚开的那一个，用户为别的页面开的抽屉不动）。同时两个引擎都监听浏览器原生 `dblclick`（2D 经 G6 的 `node:dblclick`，3D 直接在容器上监听），超窗口的慢双击也能展开而不是只开抽屉。
- **展开取景**：双击展开不再飞向被双击的单个节点（会把刚揭示的邻居甩出画布），改为数据落地后对整个新邻居取景（500ms / 1800ms 两次 `zoomToFit`）。

## 4. 验证

### 4.1 交互测试（真实组件，非源码文本断言）

新增 `web/harness-console/tests/knowledge-graph-clicks.spec.tsx`：用 G6 / 3D 两个引擎替身保留面板绑定的节点事件、`setData` / `graphData` 载荷与相机调用，真实挂载组件后回放手势（3D 的点击走容器原生监听，测试直接在容器上派发 `MouseEvent` 命中屏幕坐标）。

| 引擎 | 用例 | 断言 | 新代码 | cd2a121（改前） |
| --- | --- | --- | --- | --- |
| 2D | 单击（越过窗口后）开抽屉 | `role="dialog"` 出现 | 通过 | 通过 |
| 2D | 双击展开且不留抽屉 | 无 dialog 且节点收敛为 a–b | 通过 | **失败** |
| 2D | 慢双击收回已弹出的抽屉 | 无 dialog 且已展开 | 通过 | **失败** |
| 2D | 引擎自身 dblclick 也走展开 | 无 dialog 且已展开 | 通过 | **失败** |
| 2D | 带目标跳转只定位不 fitView | `focusElement(["b"])`、`fitView` 0 次 | 通过 | **失败** |
| 2D | 无目标打开仍 fitView 整图 | `fitView` 1 次 | 通过 | 通过 |
| 3D | 双击展开且不留抽屉 | 无 dialog 且已展开 | 通过 | **失败** |
| 3D | 浏览器原生 dblclick 也走展开 | 无 dialog 且已展开 | 通过 | **失败** |
| 3D | 带目标跳转只定位，1.4s/3.6s 兜底也不 fitView | 相机对准 1 次、`zoomToFit` 0 次 | 通过 | **失败** |
| 3D | 引擎停止时复核目标节点重新对准（无目标则 fitView） | 再次对准且 `zoomToFit` 0 次 | 通过 | **失败** |

改前代码（`git checkout cd2a121 -- knowledge-graph-panel.tsx`）下 10 个用例 **8 失败 / 2 通过**，通过的正是本轮未改动的两个行为（单击开抽屉、无目标时整图取景）。

### 4.2 发布后镜像内 bundle 校验

在 173 上直接对**运行中容器**的文件系统取证（面板 chunk = 含「该知识库还没有 Wiki 引用关系图」的那个 chunk）：

| 标记 | 改前（`knowledge-graph-3d`） | 改后（`graph-rollback-20260910`） |
| --- | --- | --- |
| `IcosahedronGeometry`（多面体） | 1 | **0** |
| `SphereGeometry` | 1 | 1 |
| 裸 `"dblclick"` 字面量（3D 新增监听） | 0 | **2** |
| `node:dblclick`（G6 事件名） | 1 | 1 |

### 4.3 环境状态

| 项 | 结果 |
| --- | --- |
| web 容器 | `agent-studio-web:graph-rollback-20260910`，healthy |
| api / worker | 未重建（`skill-catalog-20260910` / `weknora-qa-20260909-v5`，均 healthy） |
| Web 首页 | `http=200`、`time=0.009s`（容器内自测） |
| 发布前 active-run 守卫 | `0`，未推迟 |
| 前端测试基线 | 576 通过；3 个失败（`workbench-layout` ×2、`studio-client` ×1）在改动前的 HEAD 上同样失败 |
| `tsc --noEmit` | 通过 |

### 4.4 未做的验证（需人工或授权）

浏览器端到端行为验收**本轮未做**：本机 ZCode Computer Use 缺 Accessibility 授权（`list_windows` 被拒），而 console 需要登录态，无法用自动化浏览器进入知识库图谱页。请按下面三步人工确认（约 30 秒）：

1. 知识库 → Wiki → 任一页面「在图谱中查看」：跳转后视图应停在目标节点，10s 内不回整图（2D、3D 各试一次）。
2. 2D 下双击任一节点：应展开邻居，且不留下页面抽屉（可刻意放慢双击速度再试一次）。
3. 3D 下确认节点是球体、辉光通透，双击展开后取景覆盖整个新邻居。

## 5. 发布步骤（可复现）

```bash
TAG=graph-rollback-20260910
# 1) Web 镜像：本机交叉构建 amd64（见 §6 的基座限制）
docker buildx build --builder agent-deploy-http --platform linux/amd64 --provenance=false \
  -f deploy/docker/web-runtime-reuse.Dockerfile \
  -t harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:$TAG --push .
# 2) api 同 tag（纯重打标签，内容与运行中 api 同 digest，不重建）
ssh 173 'docker tag .../agent-studio-api:skill-catalog-20260910 .../agent-studio-api:'$TAG' && docker push ...'
# 3) 发布（active-run 守卫 + env 备份 + 失败回滚；只重建 web）
ssh 173 'bash /data/agent-studio-builds/graph-rollback-20260910/deploy.sh'
```

无数据库迁移，`alembic_version` 保持 0032。

## 6. 构建环境限制与新增回退 Dockerfile

构建机与 173 **都无法访问 `registry-1.docker.io`**，而 `web.Dockerfile` 以 digest 固定 `node:22-alpine`，且 harbor 内镜像的 `node:22-alpine` 是 **arm64** 变体（`$BUILDPLATFORM` 阶段用它是正确的，用作 amd64 runtime 基座则会产出一个"标称 amd64、内含 arm64 node"的坏镜像，在 173 上 `exec format error`——本轮首次构建即踩到，已作废）。

因此新增 `deploy/docker/web-runtime-reuse.Dockerfile`：依赖安装与编译仍在 `$BUILDPLATFORM`，runtime 阶段改为复用**已发布的 amd64 web 镜像**（同一 node:22-alpine 运行时、同一 `nextjs` 用户），先以 root 清空 `/app`（避免叠加上一版 bundle 里已不存在的 chunk）再铺新产物。使用时把 `RUNTIME_BASE` 指向当时线上运行的 web 镜像 tag。

遗留项：harbor `dependencies-ai/node:22-alpine` 是本次为绕开 docker.io 而推入的 **arm64** 镜像，仅供 `$BUILDPLATFORM` 阶段使用；如团队不需要，可从 harbor 删除该 tag。

## 7. 回滚

```bash
cd /data/agent-studio/docker-compose
cp .env.production.bak-graph-rollback-20260910-095736 .env.production
docker compose -p agent-studio-173 --env-file .env.production \
  -f compose.yaml -f compose.harbor.yaml -f compose.codex-runtime.yaml \
  up -d --no-build --no-deps --force-recreate web
```

上一版 web 镜像 `agent-studio-web:knowledge-graph-3d` 仍在 harbor，可直接回退（多面体造型与两个交互缺陷会一并回来）。
