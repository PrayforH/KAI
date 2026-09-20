# 173 隔离验证部署

该目录提供专用于现有 173 镜像环境的验证部署，不是新的生产部署标准。

- 工作目录：`/data/agent-studio-evolution-20260920`
- Compose project/container prefix：`agent-evolution-173`
- API `8802`，Web `3302`，原有 `8800/3301` 保持不变。
- 独立 PostgreSQL、Redis、MinIO 网络/卷；不复制业务会话或用户。
- `prepare_173.py` 生成仅 root 可读的 compose/env；已有配置时拒绝覆盖。
- 复用原环境的模型连接加密密钥，并通过 `copy_model_config.py` 复制 catalog 与模型控制面加密连接，避免明文凭据出现在命令或日志。
- Web 使用 `AUTH_COOKIE_PREFIX=harness_evolution`，防止同一 IP 不同端口共享 Cookie 时覆盖原站登录。

## 构建与初始化

API Dockerfile 在 173 上基于已经安装依赖的 Linux 镜像复制本分支源码与迁移。Web Dockerfile 读取 `web-runtime` 中的 Next standalone 构建产物；保留基座 Linux node_modules，不能用 macOS node_modules 替换。

本地构建：

```sh
uv sync --group dev --extra deepagents
cd web/harness-console
npm ci
npm run build
```

把本分支 `src/`、`migrations/`、`deploy/evolution-validation/` 和前端 standalone 构建产物传至独立部署目录。`web-runtime` 需包含 standalone 的 `server.js`、`package.json`、`.next`（含 static）以及 `public`。173 无宿主 Python 时可用 API 基座镜像运行初始化脚本。初始化脚本调用 Docker CLI，因此仅在受信任的本机验证部署中挂载 Docker socket 与 CLI。

```sh
cd /data/agent-studio-evolution-20260920
docker build -f deploy/evolution-validation/api.Dockerfile -t kai/axis-api:evolution-20260920 .
docker build -f deploy/evolution-validation/web.Dockerfile -t kai/axis-web:evolution-20260920 .
# 首次运行 prepare_173.py 后：
docker compose -f compose.json up -d postgres redis minio
docker compose -f compose.json run --rm --entrypoint /app/.venv/bin/alembic api upgrade head
# 使用 API 容器 Python 运行 init_bucket.py；按脚本说明导入加密模型配置。
docker compose -f compose.json up -d api worker web
```

已完成部署可直接使用 `docker compose -f compose.json ps` 检查状态，不要再次生成配置。健康探针是 `/healthz`。

## 保留数据的停止与恢复

```sh
cd /data/agent-studio-evolution-20260920
docker compose -f compose.json stop
# 恢复同一验证部署
docker compose -f compose.json up -d
```

需要移除隔离容器与网络但保留卷时使用 `docker compose -f compose.json down`，不要加 `-v`。停止该项目不影响原站。

业务回滚分两层：持续改进页“个人版本回退到基线”恢复个人默认版本；运行控制台“回滚到历史验证快照”恢复指定环境。两者独立，不应混淆。

环境配置含密钥，禁止提交 compose.json/api.env/web.env、复制到飞书或在终端打印。测试账号与现有站账号隔离。

## 同步 develop 后的迁移编号

主线 `0035` 创建项目表；演进迁移现为 `0036`，依赖主线 `0035`。新库或正常主线库直接 `alembic upgrade head`。

只有初次隔离部署使用了旧演进 `0035`，需要先停止 API/worker、备份数据库及 Compose 配置，再在新镜像中运行 `reconcile_legacy_revision.py --apply-after-backup`。脚本要求数据库标记恰好为 `0035`、演进表已存在、项目表和任务的项目列不存在；不满足则退出且不修改。它仅将版本标记恢复为 `0034`，随后必须立即执行 `alembic upgrade head`，补建项目表并保留演进记录。不要执行数据库 downgrade。

API 镜像构建先清除旧源码和迁移目录，并同步主线 worker 入口脚本，避免镜像中残留旧 `0035` 文件。当前依赖锁与基座相同；将来锁文件变化时必须重新构建依赖层。
