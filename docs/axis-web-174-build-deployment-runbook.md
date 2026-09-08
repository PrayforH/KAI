# AXIS Web 构建与 174 部署手册

本文用于把本仓库的 AXIS Web 前端构建并部署到验证环境 `172.20.109.174:3501`。目标读者是编码 Agent、运维 Agent 或人工操作者。

## 1. 操作范围

本流程只更新 Web 前端。必须遵守以下边界：

- 不操作 `172.20.109.173`。
- 不停止或重建 `agent-studio-174-api-1`。
- 不修改 Worker、PostgreSQL、Redis、MinIO、Docker volume、模型配置或智能体版本。
- 不覆盖或清理用户现有的本地修改。
- 不在仓库、脚本或日志中保存 SSH 密码、Token、Cookie、JWT Secret 或模型 Key。
- 未经用户明确授权，不发送真实模型任务；部署验证默认只做只读 HTTP 探测。
- 不执行 `git reset --hard`、`git checkout --`、`docker system prune -a` 或宽泛通配删除。

## 2. 环境基线

| 项目 | 值 |
| --- | --- |
| 仓库根目录 | `/Users/xiaokai/Documents/agent studio` |
| Web 工程 | `web/harness-console` |
| Dockerfile | `deploy/docker/web.Dockerfile` |
| 目标主机 | `172.20.109.174` |
| SSH 用户 | `root` |
| 正式 URL | `http://172.20.109.174:3501` |
| 临时冒烟端口 | `3599` |
| Docker 网络 | `agent-studio-174_default` |
| API 容器 | `agent-studio-174-api-1` |
| API 容器地址 | `http://api:8000` |
| Web 环境变量基线 | `agent-studio-174-web-1` |
| Web 容器端口 | `3000` |

174 是 `linux/amd64`。最终 Docker image 必须在 174 上构建，避免从 Apple Silicon 本地机器推送错误架构的运行镜像。

## 3. 凭据处理

优先使用 SSH Key：

```bash
ssh -o StrictHostKeyChecking=no root@172.20.109.174 'true'
```

如果只能使用密码，由操作者或密钥管理工具注入，不得将真实密码写进手册或脚本：

```bash
export DEPLOY_SSH_PASSWORD='<从安全渠道注入>'
export SSHPASS="$DEPLOY_SSH_PASSWORD"
sshpass -e ssh -o StrictHostKeyChecking=no root@172.20.109.174 'true'
```

下文示例使用 SSH Key。使用密码时，在每条 `ssh` 或 `scp` 命令前加 `sshpass -e`。

## 4. 发布变量

每次部署使用唯一 Release ID：

```bash
cd '/Users/xiaokai/Documents/agent studio'

export AXIS_DEPLOY_HOST='172.20.109.174'
export AXIS_DEPLOY_USER='root'
export AXIS_PUBLIC_PORT='3501'
export AXIS_SMOKE_PORT='3599'
export AXIS_RELEASE_ID="$(date +%Y%m%d-%H%M%S)"
export AXIS_ARCHIVE="/tmp/axis-web-${AXIS_RELEASE_ID}.tar.gz"
export AXIS_REMOTE_DIR="/data/axis-web-${AXIS_RELEASE_ID}"
export AXIS_IMAGE="kai/axis-web:${AXIS_RELEASE_ID}"
export AXIS_CONTAINER="axis-web-${AXIS_RELEASE_ID}"
export AXIS_SMOKE_CONTAINER="axis-web-smoke-${AXIS_RELEASE_ID}"
```

不要复用历史 Release ID。

## 5. 本地质量门禁

```bash
cd '/Users/xiaokai/Documents/agent studio'
test -f deploy/docker/web.Dockerfile
test -f web/harness-console/package-lock.json
test -f web/harness-console/src/app/page.tsx

cd web/harness-console
npm ci
npm test -- --run
npm run build
git diff --check
cd ../..
```

已有可信且与 lockfile 一致的 `node_modules` 时可跳过 `npm ci`，其余三项不可跳过。

通过标准：

- 所有 Vitest 测试通过；当前参考基线为 66 个测试文件、444 项测试。
- Next.js production build 和 TypeScript 检查成功。
- `git diff --check` 无错误。

任何一步失败都必须停止。不得删除测试、忽略退出码或放宽类型后继续部署。

## 6. 打包与上传

```bash
cd '/Users/xiaokai/Documents/agent studio'

COPYFILE_DISABLE=1 tar --no-xattrs \
  --exclude='web/harness-console/node_modules' \
  --exclude='web/harness-console/.next' \
  -czf "$AXIS_ARCHIVE" \
  deploy/docker/web.Dockerfile \
  web/harness-console

test -s "$AXIS_ARCHIVE"
ls -lh "$AXIS_ARCHIVE"

scp -o StrictHostKeyChecking=no \
  "$AXIS_ARCHIVE" \
  "${AXIS_DEPLOY_USER}@${AXIS_DEPLOY_HOST}:${AXIS_ARCHIVE}"
```

压缩包必须排除本地 `.next`，保证 174 使用上传的源码重新构建。

## 7. 远端构建

```bash
ssh -o StrictHostKeyChecking=no \
  "${AXIS_DEPLOY_USER}@${AXIS_DEPLOY_HOST}" \
  "set -euo pipefail
   test ! -e '${AXIS_REMOTE_DIR}'
   mkdir -p '${AXIS_REMOTE_DIR}'
   tar -xzf '${AXIS_ARCHIVE}' -C '${AXIS_REMOTE_DIR}'
   docker build \
     -f '${AXIS_REMOTE_DIR}/deploy/docker/web.Dockerfile' \
     -t '${AXIS_IMAGE}' \
     '${AXIS_REMOTE_DIR}'
   docker image inspect '${AXIS_IMAGE}' \
     --format 'image={{.RepoTags}} created={{.Created}}'"
```

远端日志必须显示 Next.js、TypeScript 和 Docker image 导出成功。

## 8. 部署前只读检查

必须精确确认 3501 只有一个 AXIS 容器占用，API 健康且 3599 空闲：

```bash
ssh -o StrictHostKeyChecking=no \
  "${AXIS_DEPLOY_USER}@${AXIS_DEPLOY_HOST}" 'bash -s' <<'REMOTE'
set -euo pipefail

mapfile -t axis_port_owners < <(
  docker ps --filter 'publish=3501' --format '{{.Names}}'
)

if [ "${#axis_port_owners[@]}" -ne 1 ]; then
  printf '3501 运行容器数量必须为 1，当前为 %s\n' "${#axis_port_owners[@]}" >&2
  exit 1
fi

axis_current_web="${axis_port_owners[0]}"
case "$axis_current_web" in
  axis-*) ;;
  *)
    printf '拒绝操作非 AXIS 容器：%s\n' "$axis_current_web" >&2
    exit 1
    ;;
esac

docker network inspect agent-studio-174_default >/dev/null
docker inspect agent-studio-174-web-1 >/dev/null
docker inspect agent-studio-174-api-1 \
  --format 'api={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}}'
docker inspect "$axis_current_web" \
  --format 'web={{.Name}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}} image={{.Config.Image}}'

if docker ps --filter 'publish=3599' --format '{{.Names}}' | grep -q .; then
  echo '3599 已被占用，停止部署并先确认占用者' >&2
  exit 1
fi
REMOTE
```

如果 API 不是 `running/healthy`，停止 Web 部署并先处理 API 故障。

## 9. 临时端口冒烟

启动临时容器。继承基线 Web 环境变量，但不要把这些环境变量打印到日志：

```bash
ssh -o StrictHostKeyChecking=no \
  "${AXIS_DEPLOY_USER}@${AXIS_DEPLOY_HOST}" \
  "bash -lc '
    set -euo pipefail
    test -z \"\$(docker ps -a --filter name=^/${AXIS_SMOKE_CONTAINER}\$ --format {{.Names}})\"
    docker run -d \\
      --name ${AXIS_SMOKE_CONTAINER} \\
      --network agent-studio-174_default \\
      --env-file <(docker inspect agent-studio-174-web-1 --format \"{{range .Config.Env}}{{println .}}{{end}}\") \\
      -e AUTH_PUBLIC_URL=http://172.20.109.174:${AXIS_SMOKE_PORT} \\
      -e AUTH_COOKIE_SECURE=false \\
      -e HARNESS_API_URL=http://api:8000 \\
      -p ${AXIS_SMOKE_PORT}:3000 \\
      ${AXIS_IMAGE}
  '"
```

等待首页可用：

```bash
axis_smoke_code='000'
for axis_attempt in {1..30}; do
  axis_smoke_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
    "http://${AXIS_DEPLOY_HOST}:${AXIS_SMOKE_PORT}/" || true)"
  if [ "$axis_smoke_code" = '200' ]; then
    break
  fi
  sleep 2
done
test "$axis_smoke_code" = '200'
```

执行只读探测：

```bash
curl -s -o /dev/null -w 'home=%{http_code}\n' \
  "http://${AXIS_DEPLOY_HOST}:${AXIS_SMOKE_PORT}/"
curl -s -o /dev/null -w 'icon=%{http_code}\n' \
  "http://${AXIS_DEPLOY_HOST}:${AXIS_SMOKE_PORT}/icon.svg"
curl -s -o /dev/null -w 'auth_config=%{http_code}\n' \
  "http://${AXIS_DEPLOY_HOST}:${AXIS_SMOKE_PORT}/api/auth/config"
curl -s -o /dev/null -w 'runtime_config=%{http_code}\n' \
  "http://${AXIS_DEPLOY_HOST}:${AXIS_SMOKE_PORT}/api/harness/runtime-config"
curl -s -o /dev/null -w 'session=%{http_code}\n' \
  "http://${AXIS_DEPLOY_HOST}:${AXIS_SMOKE_PORT}/api/auth/session"
curl -s -o /dev/null -w 'spaces=%{http_code}\n' \
  "http://${AXIS_DEPLOY_HOST}:${AXIS_SMOKE_PORT}/studio/spaces"
```

预期：

| 路径 | 状态码 |
| --- | --- |
| `/` | 200 |
| `/icon.svg` | 200 |
| `/api/auth/config` | 200 |
| `/api/harness/runtime-config` | 200 |
| `/api/auth/session` | 未登录时 401，属于正常结果 |
| `/studio/spaces` | 404，当前 Web 已移除该页面 |

检查临时容器：

```bash
ssh -o StrictHostKeyChecking=no \
  "${AXIS_DEPLOY_USER}@${AXIS_DEPLOY_HOST}" \
  "docker inspect '${AXIS_SMOKE_CONTAINER}' \
     --format 'status={{.State.Status}} health={{.State.Health.Status}} restarts={{.RestartCount}}'
   docker logs --tail 50 '${AXIS_SMOKE_CONTAINER}' 2>&1"
```

必须为 `running/healthy`、重启次数 0，且日志无 Next.js 启动错误。

## 10. 可回滚切换 3501

以下脚本会再次确认 3501 的唯一容器，保留旧 Web 为回滚容器，并在新 Web 不健康时自动恢复旧版：

```bash
ssh -o StrictHostKeyChecking=no \
  "${AXIS_DEPLOY_USER}@${AXIS_DEPLOY_HOST}" \
  'bash -s' -- \
  "$AXIS_RELEASE_ID" \
  "$AXIS_IMAGE" \
  "$AXIS_CONTAINER" \
  "$AXIS_SMOKE_CONTAINER" <<'REMOTE'
set -euo pipefail

axis_release_id="$1"
axis_image="$2"
axis_new_container="$3"
axis_smoke_container="$4"
axis_source_web='agent-studio-174-web-1'

mapfile -t axis_port_owners < <(
  docker ps --filter 'publish=3501' --format '{{.Names}}'
)
if [ "${#axis_port_owners[@]}" -ne 1 ]; then
  printf '3501 运行容器数量必须为 1，当前为 %s\n' "${#axis_port_owners[@]}" >&2
  exit 1
fi

axis_old_container="${axis_port_owners[0]}"
case "$axis_old_container" in
  axis-*) ;;
  *)
    printf '拒绝停止非 AXIS 容器：%s\n' "$axis_old_container" >&2
    exit 1
    ;;
esac

axis_rollback_container="${axis_old_container}-rollback-${axis_release_id}"
for axis_reserved_name in "$axis_new_container" "$axis_rollback_container"; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$axis_reserved_name"; then
    printf '容器名已经存在：%s\n' "$axis_reserved_name" >&2
    exit 1
  fi
done

docker inspect "$axis_smoke_container" >/dev/null
docker rm -f "$axis_smoke_container" >/dev/null
docker stop "$axis_old_container" >/dev/null
docker rename "$axis_old_container" "$axis_rollback_container"

axis_restore_old() {
  docker rm -f "$axis_new_container" >/dev/null 2>&1 || true
  docker rename "$axis_rollback_container" "$axis_old_container"
  docker start "$axis_old_container" >/dev/null
}

if ! docker run -d \
  --name "$axis_new_container" \
  --restart unless-stopped \
  --network agent-studio-174_default \
  --network-alias web \
  --env-file <(docker inspect "$axis_source_web" --format '{{range .Config.Env}}{{println .}}{{end}}') \
  -e AUTH_PUBLIC_URL=http://172.20.109.174:3501 \
  -e AUTH_COOKIE_SECURE=false \
  -e HARNESS_API_URL=http://api:8000 \
  -p 3501:3000 \
  "$axis_image" >/dev/null; then
  axis_restore_old
  exit 1
fi

axis_ready='false'
axis_health='unknown'
for axis_attempt in $(seq 1 30); do
  axis_probe_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 \
    http://127.0.0.1:3501/ || true)"
  axis_health="$(docker inspect "$axis_new_container" \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
  if [ "$axis_probe_code" = '200' ] && [ "$axis_health" = 'healthy' ]; then
    axis_ready='true'
    break
  fi
  sleep 2
done

if [ "$axis_ready" != 'true' ]; then
  echo '新版本健康检查失败，恢复旧版本' >&2
  axis_restore_old
  exit 1
fi

printf 'deployed=%s\nimage=%s\nprevious_name=%s\nrollback=%s\nhealth=%s\n' \
  "$axis_new_container" "$axis_image" "$axis_old_container" \
  "$axis_rollback_container" "$axis_health"
REMOTE
```

保存输出中的 `previous_name=` 和 `rollback=`，手动回滚需要这两个精确名称。

## 11. 正式环境验收

```bash
curl -s -o /dev/null -w 'home=%{http_code}\n' \
  "http://${AXIS_DEPLOY_HOST}:${AXIS_PUBLIC_PORT}/"
curl -s -o /dev/null -w 'icon=%{http_code}\n' \
  "http://${AXIS_DEPLOY_HOST}:${AXIS_PUBLIC_PORT}/icon.svg"
curl -s -o /dev/null -w 'auth_config=%{http_code}\n' \
  "http://${AXIS_DEPLOY_HOST}:${AXIS_PUBLIC_PORT}/api/auth/config"
curl -s -o /dev/null -w 'runtime_config=%{http_code}\n' \
  "http://${AXIS_DEPLOY_HOST}:${AXIS_PUBLIC_PORT}/api/harness/runtime-config"

ssh -o StrictHostKeyChecking=no \
  "${AXIS_DEPLOY_USER}@${AXIS_DEPLOY_HOST}" \
  "docker inspect '${AXIS_CONTAINER}' \
     --format 'web={{.State.Status}} health={{.State.Health.Status}} restarts={{.RestartCount}} image={{.Config.Image}}'
   docker inspect agent-studio-174-api-1 \
     --format 'api={{.State.Status}} health={{.State.Health.Status}} restarts={{.RestartCount}}'
   docker logs --tail 80 '${AXIS_CONTAINER}' 2>&1"
```

通过标准：

- 首页、图标、鉴权配置、runtime config 均为 200。
- Web 与 API 均为 `running/healthy`，重启次数为 0。
- Web 日志包含 `Ready`，没有启动或代理错误。
- 浏览器强制刷新后，首页、任务历史、搜索、智能体选择、模型选择和输入框正常显示。
- 只有用户明确授权后，才发送一条低风险任务验证流式输出。

## 12. 手动回滚

先列出容器并核对新容器、旧容器原名称和回滚容器：

```bash
ssh root@172.20.109.174 \
  "docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}' | grep '^axis-'"
```

然后设置三个精确变量：

```bash
export AXIS_FAILED_CONTAINER='<第 10 节 deployed 值>'
export AXIS_ROLLBACK_CONTAINER='<第 10 节 rollback 值>'
export AXIS_PREVIOUS_NAME='<第 10 节 previous_name 值>'
```

执行回滚：

```bash
ssh -o StrictHostKeyChecking=no \
  "${AXIS_DEPLOY_USER}@${AXIS_DEPLOY_HOST}" \
  'bash -s' -- \
  "$AXIS_FAILED_CONTAINER" \
  "$AXIS_ROLLBACK_CONTAINER" \
  "$AXIS_PREVIOUS_NAME" <<'REMOTE'
set -euo pipefail

axis_failed_container="$1"
axis_rollback_container="$2"
axis_previous_name="$3"

test -n "$axis_failed_container"
test -n "$axis_rollback_container"
test -n "$axis_previous_name"
docker inspect "$axis_failed_container" >/dev/null
docker inspect "$axis_rollback_container" >/dev/null

docker stop "$axis_failed_container" >/dev/null
docker rm "$axis_failed_container" >/dev/null
docker rename "$axis_rollback_container" "$axis_previous_name"
docker start "$axis_previous_name" >/dev/null

for axis_attempt in $(seq 1 30); do
  axis_probe_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 \
    http://127.0.0.1:3501/ || true)"
  if [ "$axis_probe_code" = '200' ]; then
    docker inspect "$axis_previous_name" \
      --format 'restored={{.Name}} status={{.State.Status}} health={{.State.Health.Status}}'
    exit 0
  fi
  sleep 2
done

echo '旧版本恢复后仍未通过首页探测' >&2
exit 1
REMOTE
```

该操作删除失败的新容器，但保留其 image 与远端源码目录，便于继续排查。

## 13. 常见故障

### 首页正常但发送失败

```bash
curl -i http://172.20.109.174:3501/api/harness/runtime-config
ssh root@172.20.109.174 \
  "docker logs --tail 150 '${AXIS_CONTAINER}' 2>&1
   docker logs --tail 150 agent-studio-174-api-1 2>&1"
```

确认：

- Web 配置为 `HARNESS_API_URL=http://api:8000`。
- Web 位于 `agent-studio-174_default` 网络。
- API 可通过网络别名 `api` 访问且健康。
- 浏览器发送到 Web BFF 的 `/api/agui`，不是直接请求容器内部 API。
- 401 是否来自用户登录过期；它不等同于模型网关 401。
- 模型路由应在“设置 → 模型管理”维护，不要把模型密钥塞进 Web 环境变量。

### 登录反复 401

浏览器地址与 `AUTH_PUBLIC_URL` 必须完全一致：`http://172.20.109.174:3501`。当前是 HTTP 验证环境，因此 `AUTH_COOKIE_SECURE=false`。切换 HTTPS 时必须同时改为真实 HTTPS URL 和 `AUTH_COOKIE_SECURE=true`。

### 新样式未出现

- 强制刷新浏览器。
- 检查 3501 实际容器与 image tag。
- 确认打包时排除了 `.next`，并在 174 重新执行 Docker build。
- 不要因为浏览器缓存问题重启 API。

### 3599 被占用

先识别占用者，不要直接删除未知容器：

```bash
ssh root@172.20.109.174 \
  "docker ps --filter 'publish=3599' --format '{{.Names}}|{{.Image}}|{{.Ports}}'"
```

只有确认它是已废弃的 `axis-web-smoke-*` 后才能删除；否则选择其他空闲端口，并同步修改端口映射和冒烟环境的 `AUTH_PUBLIC_URL`。

## 14. 发布后清理

在用户完成视觉确认和一次真实任务验证前，不要删除回滚容器与旧 image。

只读列出候选项：

```bash
ssh root@172.20.109.174 \
  "docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}' | grep '^axis-'
   docker images --format '{{.Repository}}:{{.Tag}}|{{.CreatedSince}}|{{.ID}}' | grep '^kai/axis-'"
```

清理时必须显式给出完整容器名、image tag 和远端目录。禁止宽泛通配，禁止 `docker system prune -a`。

## 15. 可直接交给其他工具的任务指令

```text
请严格按照下面的手册构建并部署 AXIS Web：
/Users/xiaokai/Documents/agent studio/docs/axis-web-174-build-deployment-runbook.md

执行要求：
1. 只更新 172.20.109.174:3501 的 Web；不触碰 173，不重建或重启 174 的 API、Worker、数据库、Redis、MinIO。
2. 保留本地已有修改，不执行 reset、checkout 或清理工作区。
3. 完整执行测试、Next.js production build 和 git diff --check，任何失败都停止。
4. 最终 Docker image 在 174 上构建。
5. 先在 3599 启动临时容器并完成只读冒烟，再切换 3501。
6. 切换前精确识别 3501 的唯一容器，禁止按模糊名称停止或删除。
7. 保留旧 Web 为回滚容器；新版本不健康时自动恢复旧版本。
8. 不打印或写入密码、Token、Cookie、模型 Key 或容器完整环境变量。
9. 未经明确授权，不发送真实模型任务。
10. 全程报告关键进度；完成后给出 release、image、新容器、回滚容器、测试和健康检查结果。

SSH 凭据由操作者通过 SSH Key 或安全环境变量另行提供，不得写入仓库。
```

## 16. 完成报告模板

```text
AXIS Web 已部署到 http://172.20.109.174:3501

- Release ID：<release-id>
- Image：<image:tag>
- 新容器：<container>
- 回滚容器：<rollback-container>
- 本地测试：<测试文件数>/<测试项数> 通过
- 本地 production build：通过
- 远端 Docker build：通过
- 临时端口冒烟：通过
- 正式 Web：running / healthy / restart 0
- 174 API：running / healthy / restart 0
- 173：未操作
- 真实任务发送：未执行；或填写用户已授权的验证结果
```
