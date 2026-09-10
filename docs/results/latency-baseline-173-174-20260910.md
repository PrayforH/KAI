# KAI WORKBENCH 基础时延排查 · 173/174（2026-09-10）

- 日期：2026-09-10（凌晨，00:34–00:55）
- 测试主体：`172.20.109.173`（KAI WORKBENCH，Web `:3301`、API `:8800`，镜像 `weknora-kb-20260910-cardmenu`）
- 对照组：`172.20.109.174`（KAI WORKBENCH Web `:3501`/API `:8800` 老版本实例 + WeKnora `:8180`）
- 测试方式：从开发机（`192.168.0.105`）对外 HTTP 采样，以及 SSH 进入 173 后在宿主机/容器内部测量对照
- 目的：回答「为什么感觉整个项目卡卡的」，区分网络开销与服务器处理开销

> 174 的 root 登录（key 与密码均被拒）不可用，174 侧只做 HTTP 黑盒采样；173 可 SSH（本机专用 key），完成了内部纵深测量。

## 1. 结论（TL;DR）

1. **应用服务器本身不慢**。173 API 本地处理耗时在个位数到 25ms；WeKnora、Postgres、Redis、静态资源等服务端内部全部毫秒级。
2. **用户侧到这两台机器存在固定的 ~70–90ms「数据面」时延**（TCP 握手 ~1ms 即完成，但连接建立后的数据往返固定 ~70–90ms，连 SSH banner 都如此）。每个 HTTP 请求都要付这笔钱，页面加载有大量请求时会明显觉得「卡」。这是网络/网关路径特性，不是代码问题，173/174 一视同仁。
3. **173 与 Milvus 集群同机**（milvus02，16 个 milvus 容器 + easyllm + cadvisor）。测量期间 host load 出现过 12–16 的高峰（其他任务在跑/部署），此时 API p95 一度飙到 200–900ms；高峰回落后 p95 恢复 90–126ms。**host 资源争抢是「偶发卡顿」的来源**。
4. 173 Web/API 已是新 tag（healthy）；**3 个 worker 仍是 8 小时前的 `weknora-qa-20260909-v5`**，与 api/web 镜像不一致——本次未升级 worker（可能是有意为之），建议确认。

## 2. 服务端内部耗时（已排除网络）

在 173 上（SSH）或容器内直接测量：

| 项目 | 结果 |
| --- | --- |
| API `/healthz`（宿主机→127.0.0.1:8800，10 次） | 2–5ms |
| API `/v1/auth/config` | 2ms |
| API `/openapi.json`（大响应体本地） | 13–24ms |
| 173 → 174 `/healthz`（跨机同内网） | 3–4ms |
| 173 → 174 WeKnora `:8180`（api 容器内） | 1ms（首次 46ms 冷启动） |
| Postgres `SELECT 1` / Redis PING | 正常（毫秒级） |
| Web 静态资源（SSR/HTML 已 prerender 命中缓存） | 单资源 0.08–0.35s，见 §5 |

**服务端处理开销远低于用户感知的 ~80ms+，因此瓶颈不在应用代码的单个接口处理。**

## 3. 网络路径分析（卡感主要来源）

| 度量 | 173 | 174 |
| --- | --- | --- |
| TCP connect | ~1ms | ~1ms |
| SSH banner 到达（同连接首次数据） | ~70–90ms | ~70–90ms |
| `/healthz` ttfb（25 次，p50） | 76.9ms | 79.5ms |
| `/v1/auth/config` ttfb（p50） | 84.5ms | 84.4ms |
| 同 p95 | 91.7–93.8ms | 106–162ms |
| 同 max | 356–544ms | 150–540ms |

- TCP 三次握手几乎瞬时完成（~1ms），说明链路不「远」；但**数据包往返固定 ~70–90ms**（SSH banner 也要 70–90ms），是典型的安全网关/WAN 优化设备对数据面审计/加速引入的固定开销。
- 结论：**每个请求的基础成本约 80ms（网络）+ 服务端处理（<25ms）**。页面/前端每发起一个串行请求就叠加一次，多请求场景体感即「卡」。

## 4. 173 宿主机资源（偶发卡顿来源）

| 指标 | 观察 |
| --- | --- |
| 机器角色 | milvus02：agent-studio + 全套 Milvus 集群(16 容器) + easyllm + cadvisor 同机 |
| host load | 测量期间 16.6 → 4.7 → 12.7 → 15.8 → 回落 3.5–6.9（有部署/其他任务在跑） |
| CPU idle | 94–95%（高峰时刻 milvus 进程 50–87% CPU、cadvisor 18–75%） |
| agent-studio 容器 CPU | api 0.16%、web 0.61%、worker ≈1%、postgres 0.22%、redis 0.76%（全低） |
| 内存/交换 | 内存充足（avail 143GB+）；swap 用了 8.3GB |
| 磁盘 IO wait | vmstat 显示 wa≈0–1%，未见磁盘瓶颈 |

- agent-studio 容器 CPU 占用极低，**应用不因 CPU 争用而慢**；但 milvus/cadvisor 的高 CPU 窗口与 173 API p95 尖峰（200–900ms）在时间上吻合，说明**同机负载高峰会拖累 API 响应抖动**。

## 5. 前端页面加载视角

- 首页 HTML 已 prerender（`x-nextjs-cache: HIT`），返回 200 快。
- 首页引用了 23 个静态 JS/CSS 资源，合计约 2.5MB。
- 单资源 ttfb 全部 ≤0.35s（主要含 70–90ms 网络段），无一超过 1s。
- keep-alive 单连接顺序取 10 个资源 ≈0.96s；每个资源新建连接 ≈2.46s——**新建连接成本高（每次多 ~150ms）**。
- Web 为 HTTP/1.1（无 HTTP/2），浏览器按约 6 连接并行；对 23 个资源而言连接复用与并行度直接决定首屏等待。
- 一次并行全量拉取中出现过单请求停滞到 20s 超时（偶发），与同机负载高峰相关，复测未见。

## 6. 现有部署状态核对（173）

| 服务 | 镜像 tag | 状态 |
| --- | --- | --- |
| api | `weknora-kb-20260910-cardmenu` | Up 9 min（healthy） |
| web | `weknora-kb-20260910-cardmenu` | Up 9 min（healthy） |
| worker ×3 | `weknora-qa-20260909-v5`（旧） | Up 8 h（healthy） |
| postgres/redis/minio/otel | 各自稳定 | healthy |
| migrate/seed | 已完成退出 | Exited(0) |

- api/web 已就绪（本次时延测试即在更新后的 api/web 上进行）。
- **worker 未随本次升级**：若 worker 承担知识问答/检索任务，且代码契约与 api 新版本有依赖，应确认是否需要一并升级对齐。

## 7. 建议

1. **确认用户访问路径的网络固定时延**：在用户实际浏览器 DevTools 里看任意请求的 Waiting (TTFB)。若普遍 ~80ms 且与服务器负载无关，即网络固定成本——这是卡感的第一来源。
2. **缓解多请求排队**：Web 目前 HTTP/1.1 + 每请求 ~80ms 网络开销。可评估启用 HTTP/2（多路复用单连接），或把静态资源与 API 就近/前置缓存，减少跨网关的请求次数。
3. **避免应用与 Milvus 重负载同机**：173 同时承载 Milvus 集群。至少对 milvus 容器设置 CPU/内存 cgroup 上限，避免其负载高峰挤占 API。
4. **对齐 worker 镜像**：确认 3 个 worker 是否应升级到 `weknora-kb-20260910-cardmenu`（与 api/web 同 tag），避免 api/worker 版本漂移。
5. **复测口径**：本报告 173 数据取自更新完成后的稳定时段；如需与「卡」的现场对照，建议在同一用户网络内、带真实登录态再抽测知识库/会话接口。

## 8. 原始数据

- 服务端本地处理：见 §2（10–25 次/端点）。
- 对外采样：`/healthz`、`/v1/auth/config`、openapi、静态资源均 15–25 次/端点，p50/p95/max 见 §3。
- 173 宿主机：`uptime`、`ps --sort=-pcpu`、`docker stats`、`vmstat`、`iostat` 快照见正文。
