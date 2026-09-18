# 跨主机 worker 上线：174 主机 1 个 worker 加入 173 环境（2026-09-19）

## 1. 拓扑

```
173 环境（控制面）
├─ api / web(3301) / postgres / redis / minio(59000,已开局域网) / otel(14318,已开局域网)   @173
├─ worker ×3（173 本机，compose --scale worker=3）
└─ worker ×1（174 主机独立容器 axis-worker-for-173，加入 173 的 Redis 队列）
     ├─ Postgres / Redis / MinIO / OTLP → 173
     ├─ weknora → 174 本机（http://172.20.109.174:8180，原有安排，173 的 web 经 173 API 使用）
     └─ OpenSandbox → 115:8090（174 实测可达）
```

174 自己的环境（api + worker×3 + web + 3501 控制台）保持独立运行，互不影响。

## 2. 部署细节

- 容器：`axis-worker-for-173`，`--restart unless-stopped`，镜像 `kai/axis-api:develop-bfa9f8a`
  （174 本地已有，内容与 173 的 api 逐文件一致），`--entrypoint entrypoint-worker`，
  **不发布任何端口**（worker 指标端口 8001 留在容器网络内）。
- env：复制 173 worker-1 全量 104 项，仅改写 4 个端点：
  - `HARNESS_DATABASE_URL → postgresql+asyncpg://…@172.20.109.173:55432/harness`
  - `HARNESS_REDIS_URL → redis://172.20.109.173:56379/0`
  - `HARNESS_MINIO_ENDPOINT → 172.20.109.173:59000`
  - `HARNESS_OTLP_ENDPOINT → http://172.20.109.173:14318/v1/traces`
  - `HARNESS_WEKNORA_BASE_URL` 保持 `http://172.20.109.174:8180`（174 本机）
  - env 文件持久化在 174 `/data/worker-for-173.env`（600）。
- 173 侧为跨主机开放的两个中间件端口（用户授权）：MinIO `0.0.0.0:59000`、OTLP
  `0.0.0.0:14317/14318`。OTLP 重建此前一直失败的原因是 `compose.harbor.yaml` 对全部服务
  `pull_policy: always`，而 Harbor 上该 collector tag 已删；在
  `compose.develop-bfa9f8a.yaml` 追加 `otel-collector: pull_policy: never` 后用本地镜像重建成功。
  `compose.yaml` 改动有备份 `compose.yaml.bak-lan`。

## 3. 决定性验证

方法：停掉 173 的 3 个 worker（活动任务=0 时），使 174 的远程 worker 成为 173 队列**唯一**
认领者，经 173 API 入队真任务：

- run `run_f8dd0112…`（public-opinion-agent 0.3.13）→ **succeeded**；
- `sandbox.provisioned`：`opensandbox-deferred / delegated / lease_id / trust_watermark=safe`；
- 173 DB `sandbox_leases`：released；平台沙箱已销毁（DELETE 204）；
- **归属物证**：174 容器日志（缓冲刷新后）记录了它对 115 的完整数据面调用
  （`/v1/sandboxes/7e92d98d…/proxy/44772/files/download?path=/workspace/run_f8dd0112…` →
  `DELETE /v1/sandboxes/7e92d98d…` 204），而 173 三个 worker 本地均无该 run 的工作目录；
- 验证后 173 的 3 个 worker 已恢复（healthy），`live leases: 0`。

日常混合运行时，认领分配由 Redis 原子出队决定（`storage/redis.py` 的 Lua 脚本），4 个
worker 天然分摊，无需配置。

## 4. 已知注意事项（按影响排序）

1. **worker 日志是缓冲的**：`docker logs` 可能延迟数十秒才出现内容（python stdout 块缓冲）。
   排障时以 DB/事件/指标为准，或等缓冲刷新。
2. **会话门 TTL 与队列可见性超时相等（都 60s）**：主机假死 >60s 时两个租约同时到期竞态。
   建议后续把门 TTL 调为 2× 可见性超时（`composition.py` 附近一行改动）。
3. **主机时钟**：stuck-run 判定用各主机本地时钟对比 DB 时间戳，最紧窗口 30s
   （`stuck_cancelling_seconds`）。两台主机需 NTP 对齐（运维确认）。
4. memory 索引/抽取任务每副本都跑，跨主机会有 N× embedding 花费（幂等、无正确性影响）。
5. 174 主机上该 worker 与 174 自己的 3 个 worker 是**不同环境**的进程，互不通信；
   `docker rm -f axis-worker-for-173` 即可摘除。

## 5. 回滚

```bash
# 摘除跨主机 worker
ssh root@172.20.109.174 'docker rm -f axis-worker-for-173'
# （可选）收回 173 的局域网端口
ssh root@172.20.109.173 'cd /data/agent-studio/docker-compose && cp compose.yaml.bak-lan compose.yaml \
  && docker compose --env-file .env.production -f compose.yaml -f compose.harbor.yaml \
     -f compose.codex-runtime.yaml -f compose.develop-bfa9f8a.yaml up -d minio'
```
