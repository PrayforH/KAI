# 116 验收环境部署（2026-11-04）

用户要求把一套搬到 **172.20.109.116**（79 有网络波动，影响验收）。本机与 79 都到不了
100.x；116 实际是 **109.116**，本机 VPN 已下发该 /32，可直连。

## 主机

`ubuntu-116`，Ubuntu，64 核 / 125G 内存 / 439G 系统盘 + 3.7T `/data`，Docker 29.2.1 +
Compose v5.1.0。**无外网出口**（baidu/dockerhub 均不可达），只能访问内网：harbor:5000、
172.20.109.174:4000（模型网关）、172.20.109.111:13000（cube 沙箱）。

## 部署内容

发布目录 `/data/kai-116-20261104`（compose.private.json + kai-116-https.conf + tls/），
项目名 `kai-116-20261104`：

| 服务 | 镜像 |
|---|---|
| postgres | `kai/postgres:18.1-pgvector-79-20261013` |
| redis | `harbor.shdata.com:5000/dependencies-ai/redis:8.4.0` |
| minio | `harbor.shdata.com:5000/dependencies-ai/minio/minio:RELEASE.2025-04-22T22-12-26Z` |
| api / worker | `kai/superagent-api:20261103-4e559c28` |
| web | `kai/superagent-web:20261104-dd6ce211` |
| https | `nginx:1.27-alpine`（自签证书，CN/SAN = 172.20.109.116） |

镜像从 79 `docker save | gzip` 经本机流式搬到 116（116 无外网、与 79 不通）。

**相对 79 的差异**（都是环境不可达导致的必要改动）：
- `HARNESS_SANDBOX_PROVIDER=cubesandbox`（79 用的 opensandbox 在 79:24090，116 到不了），
  `HARNESS_SANDBOX_EXTRA_PROVIDERS` 清空。
- `HARNESS_KNOWLEDGE_MCP_PUBLIC_URL` / `HARNESS_MEMORY_MCP_PUBLIC_URL` 指向
  `http://172.20.109.116:8800/mcp/...`（原来指向 79）。
- `AUTH_PUBLIC_URL=https://172.20.109.116:3543`；`KAI_DESKTOP_VIEWER_URL` 清空。
- 未部署：feishu、weixin（缺外部凭据且不影响验收）、computer-control/viewer/desktop
  （需要 guest qcow2 与控制面；116 有 /dev/kvm，可作为下一步）。
- 去掉 compose 里的 `migrate` 服务（镜像 entrypoint 是 api 服务，`up` 会等它「完成」而挂住），
  迁移改为一次性手工执行。

## 库初始化（全新库）

`alembic upgrade head` 在 0055 复现 `DuplicateColumnError: idempotency_key`（与既有记录一致）；
按既有配方改为：`create_schema`（`Base.metadata.create_all` + vector 扩展）→ `alembic stamp head`，
`alembic_version = 0065`。应用库名沿用 URL 里的 `harness_174_20260908`（postgres 容器默认建的是
`harness`，需手工建库后迁移）。

## 验证

- 容器：postgres/redis/minio/api/worker/web 全 healthy，https 启动正常。
- `http://172.20.109.116:3501/ = 200`、`https://172.20.109.116:3543/ = 200`（返回 codex 主题页）、
  `/login = 200`、api `:8800/healthz = 200`、api 启动日志 `Application startup complete`。
- 上游连通：模型网关 `172.20.109.174:4000 = 200`、cube `172.20.109.111:13000 = 401`（未带 key，符合预期）。

## 待用户验收 / 下一步

1. 浏览器打开 `https://172.20.109.116:3543`（自签证书需手动信任），**首个注册用户自动获得平台智能体**。
2. 未做：电脑（VM/本地）栈、feishu/weixin —— 需要时再搬。
