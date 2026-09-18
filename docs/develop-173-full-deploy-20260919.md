# 173 全面对齐 develop 发布记录（2026-09-18/19）

## 0. 背景与用户纠偏

第一轮只对齐了 173 的 API（16 个差异文件叠加）。用户指出 **173 的文件列表没有缩略图**——
原因：173 的 Web 一直是 Harbor 的 `develop-20260916-cf97d79`（9-16 构建），而缩略图摘要行
（`8802592`/`e3fec9a`/`cad81e6`）、新标签页按钮（`0e63eff`）是 9-17/9-18 才进 develop 的
控制台提交。本轮补上 Web：**用 develop 的 Web 全量构建部署**，173 与 174 的 API/Web 内容
现已一致。

## 1. 部署内容

| 组件 | 173 部署后 | 方式 |
| --- | --- | --- |
| api + worker ×3 | `kai/axis-api:develop-bfa9f8a` | 以 `governance-20260918` 为基座叠加 16 个差异文件（15 改 1 新增），镜像内 16/16 md5 与 develop 一致；`harness` 293/293 等于 develop |
| web（3301，即 compose 的 `web-1`，用户实际访问的控制台） | `kai/axis-web:develop-bfa9f8a`（`c19f2390b208`，与 174:3501 同一镜像 `20260918-185931` 仅改 tag） | 174 构建好的镜像 `docker save \| load` 直传（173 本机构建在 Node 下载步骤必失败） |
| quality-sync / otel / postgres / redis / minio | 未动 | 与 174 一样保持原镜像 |

部署机制：沿用 173 既有的临时 override（`/data/agent-studio/docker-compose/compose.develop-bfa9f8a.yaml`，
由 `compose.governance.yaml` 派生，追加 `web` 服务覆盖），以
`compose.yaml + harbor + codex-runtime + 该 overlay` 重建 api/worker/web。

**迁移：没有单独做**。173 的 `alembic_version` 在上一轮治理发布时已是 `0033`，本次叠加层
不含迁移文件，也未运行 migrate 容器。迁移是"每个环境各自一次"的（174 当时需要迁是因为它
还停在 0032）。

## 2. 事故防护核查（9-14 教训）

构建/切换 Web 前先确认 develop 源码包含 173 用户可见的全部在途功能，避免再次窄构建覆盖：

- `wikiContentInstructions`（2 源文件）、`分块设置`、`Wiki 设置` 已提交在 develop——9-14
  事故中"工作区未提交"的 wiki 表单此后已入库；
- 缩略图摘要行提交 `e3fec9a`、新标签页 `0e63eff` 均为 develop 祖先；
- 镜像内核对：`分块设置`/`wikiContentInstructions`/`rail-preview-action` 全部命中。

## 3. 验证证据

API（上轮已做）：api + 3 worker healthy；`/healthz` 200；provider 级探针走完
OpenSandbox 数据面生命周期；经 API 真任务 `run_80f7ebe3…` succeeded，
`opensandbox-deferred / container / delegated` + `lease_id/lease_epoch`，租约 released。

Web（本轮）：

- 容器 `kai/axis-web:develop-bfa9f8a` **healthy**；`/`、`/icon.svg`、`/api/auth/config`、
  `/api/harness/runtime-config` 全 200；
- 从 3301 实际抓取服务端 bundle：`rail-preview-action=1`（新标签页按钮）、
  `artifact-summary-thumbs=1`、`artifact-thumb=1`（**缩略图**）；
- 镜像内 `分块设置` / `wikiContentInstructions` 命中（知识库 wiki/分块表单不丢）。

提示：前端 bundle 是哈希文件名，浏览器需**硬刷新**才能看到新界面。

## 4. 173 与 174 的剩余差异（有意保留）

| 项 | 174 | 173 |
| --- | --- | --- |
| 沙箱后端 | CubeSandbox @111 | OpenSandbox @115（worker 可直连 115:8090） |
| API 镜像血统 | 基座 `obs-egress-20260918c` | 基座 `develop-20260916-cf97d79`（内容已逐文件对齐 develop） |
| compose web-1（3301） | 旧构建，用户实际用 3501 独立容器 | **已更新为 develop 构建**，3301 即控制台 |

两台机器 `/app/.venv` 依赖清单已核对完全一致（223 项）。

## 5. 回滚

```bash
cd /data/agent-studio/docker-compose
# 1) env：恢复部署前备份（ls .env.production.bak-web-develop-* / .env.production.bak-develop-bfa9f8a-*）
# 2) 把 compose.develop-bfa9f8a.yaml 里三个 image 改回 governance-20260918 / 删除 web 段
# 3) 重建
docker compose --env-file .env.production -f compose.yaml -f compose.harbor.yaml \
  -f compose.codex-runtime.yaml -f compose.governance.yaml up -d --no-deps \
  --force-recreate --scale worker=3 api worker web
```

## 6. 跨设备多 worker（已分析，未实施）

结论：代码层支持多主机副本——任务认领是 Redis 原子 Lua（`storage/redis.py:55-72`）、Run
归属靠 Postgres fencing、会话门是 Redis 实现（跨主机互斥、Redis 不可达 fail-closed）、
事件/steering/取消全部持久化、SDK 会话镜像存 Postgres（跨主机可续跑）。无需 leader 选举。

实施前需要解决（按阻塞排序）：

1. **MinIO 只绑 `127.0.0.1:59000`**：其它主机不可达（实测 174→173:59000 不通）。worker 直连
   MinIO 上传产物/工作区快照，这是硬阻塞；放开到局域网是安全决策，待确认。
2. OTLP collector、weknora、memory embedding 等在 173 是 compose 内部域名，外部 worker 需要
   可达地址；worker 指标端口 `0.0.0.0` 且无鉴权，需防火墙/网段限制。
3. 建议加固：会话门 TTL（=可见性超时 60s）改为 ≥2×，避免两租约同时到期竞态；生产容器缺失
   `session_gate` 时应报错而非静默退化进程内门；各主机 NTP 对齐（`stuck_cancelling_seconds=30`
   是最紧窗口）；各 worker 主机装同一份 Codex/Claude CLI（deferred 模式模型进程本地跑）。
4. 维护任务每副本都跑：除 memory 索引/抽取（N× embedding 花费）与凭据租约（进程内不可跨主机
   回收）外均无害或幂等；可后续用 PG advisory lock 去重。

待用户提供：哪几台设备各跑几个 worker、以及 MinIO 是否允许对局域网暴露。
