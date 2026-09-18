# 沙箱形态测试用例（174 环境，2026-09-18）

针对"沙箱的几种使用形态"给出可执行用例。每个用例只证明一件事：那种形态**真的生效了**，而不只是 Run 成功。

## 0. 环境事实（先读，决定哪些用例能直接跑）

174 当前设置（`docker inspect agent-studio-174-api-1`）：

| 键 | 值 |
| --- | --- |
| `HARNESS_SANDBOX_PROVIDER` | `cubesandbox` |
| `HARNESS_SANDBOX_EXECUTION_MODE` | `worker_cli_deferred` |
| `HARNESS_SANDBOX_EGRESS_ENFORCEMENT` | 未设置 → `declared`（按 Agent 由 profile 决定） |
| `HARNESS_CUBESANDBOX_IDLE_POLICY` | 未设置 → `destroy` |
| `HARNESS_SANDBOX_EXTRA_PROVIDERS` | 未设置 → 只服务 cubesandbox |
| `HARNESS_CUBESANDBOX_TEMPLATE` | `tpl-f116a5f3d1c442b2b1690f4d` |
| `HARNESS_CUBESANDBOX_API_URL` / `PROXY_URL` | `http://172.20.109.111:13000` / `:80` |
| `HARNESS_CUBESANDBOX_CLAUDE_CLI_VERSION` | `2.1.259` |

已存在的测试对象：

- 探针 Agent `egress-gray-probe` 0.1.0（owner `user_44229d56651b44a8b91f47dc446dc275`，绑定模型 `minimax-m3`）
- 它的 `test` 环境部署快照 `deployment_snapshot_64408cd5…`，**钉在 `cubesandbox-egress-enforced` v1**
- 目录 profile 与后端对应：`cubesandbox-private`(declared/cubesandbox)、`cubesandbox-egress-enforced`(enforced/cubesandbox)、`gvisor-production`(gvisor)、`opensandbox-gvisor`(opensandbox)、`e2b-public-egress`(e2b)、`local-development`/`isolated-default`(local)

### 公共准备（在 174 宿主机执行）

```bash
API=http://127.0.0.1:8800
TOKEN=$(docker exec agent-studio-174-api-1 printenv HARNESS_API_BEARER_TOKEN)
CUBE_KEY=$(docker exec agent-studio-174-api-1 printenv HARNESS_CUBESANDBOX_API_KEY)   # 不要 echo/落盘
OWNER=user_44229d56651b44a8b91f47dc446dc275
H=(-H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: local" -H "X-User-ID: $OWNER" -H "Content-Type: application/json")

mk_session() {  # $1 = '"environment":"test"' 或 '"agent_version":"0.1.0"'
  curl -sS "${H[@]}" -X POST "$API/v1/sessions" \
    -d "{\"agent_name\":\"egress-gray-probe\",$1}" |
  docker exec -i agent-studio-174-api-1 python -c 'import json,sys;print(json.load(sys.stdin)["session_id"])'
}

mk_run() {  # $1 = session_id, $2 = prompt
  curl -sS "${H[@]}" -H "Idempotency-Key: case-$(date +%s%N)" \
    -X POST "$API/v1/sessions/$1/runs" -d "{\"prompt\":$2}" |
  docker exec -i agent-studio-174-api-1 python -c 'import json,sys;print(json.load(sys.stdin)["run_id"])'
}

wait_run() {  # $1 = run_id，最多等 6 分钟
  for i in $(seq 1 36); do
    s=$(curl -sS "${H[@]}" "$API/v1/runs/$1" |
        docker exec -i agent-studio-174-api-1 python -c 'import json,sys;print(json.load(sys.stdin)["status"])')
    echo "  ${i}0s status=$s"
    case "$s" in succeeded|failed|cancelled|timed_out|rejected) return 0;; esac
    sleep 10
  done
}

events() { curl -sS "${H[@]}" "$API/v1/runs/$1/events" | grep '^data: ' | sed 's/^data: //'; }
provisioned() { events "$1" | docker exec -i agent-studio-174-api-1 python -c '
import json,sys
for line in sys.stdin:
    e=json.loads(line)
    if e["type"]=="sandbox.provisioned": print(json.dumps(e["payload"],ensure_ascii=False))
'; }
ls_sandboxes() {  # 列出平台实例及其 harness.* 标记（2026-09-18 在 174 实测的形状）
  docker exec -i agent-studio-174-api-1 python - <<'PY'
import asyncio
from harness.config import Settings
from harness.sandbox.cubesandbox import build_cubesandbox_provider

async def main():
    provider = build_cubesandbox_provider(Settings())
    for sandbox_id, metadata in await provider._client.list_managed():
        marks = {k: v for k, v in metadata.items() if str(k).startswith("harness")}
        print(sandbox_id, marks)

asyncio.run(main())
PY
}
```

> 实测形状：`list_managed()` 返回 `[(sandbox_id, metadata)]`，`sandbox_id` 是 32 位十六进制；metadata 至少含 `harness.tenant` / `harness.session` / `harness.run`，启用常暖/暂停时多一个 `harness.keep`，施加出口策略时多一个 `harness.egress`（策略指纹）。空平台返回 `[]`。

---

## 用例总表

| # | 形态 | 判定这件事 | 174 可直接跑？ |
| --- | --- | --- | --- |
| A | 同世界（local，无隔离） | 无沙箱时不产生实例，enforcement=none | 需临时部署/本地容器 |
| B | 延迟代理 microVM（cubesandbox + deferred） | 工具在 microVM 内执行，delegated | ✅ |
| C | 出口策略：按 Agent 灰度（declared vs enforced） | 同 Agent 两种环境行为相反 | ✅ |
| D | 出口策略：全局开关 | 未钉 profile 的 Run 也受控 | 需改 env + 重建 |
| E | 常暖跨轮复用（keep_warm） | 第二轮复用同一实例 | 需改 env + 重建 |
| F | pause/resume（进程态跨轮存活） | 暂停的进程在下一轮继续 | 需改 env + 重建 |
| G | 远程 CLI 形态（remote_cli） | CLI 在沙箱内跑通 | ✅（provider 级） |
| H | 多后端路由与"未启用即拒绝" | 钉住未启用后端被拒 | ✅（拒绝路径） |
| I | 观测：失败 Run 的平台日志 | `sandbox.logs` 落到事件 | ✅（本轮发现并修复缺陷，见该节） |
| J | 租约与孤儿回收 | 租约生命周期 + 无主实例被回收 | ✅（正向已验证） |

---

## A. 同世界形态（local provider）

**目的**：确认"无隔离"是显式形态，而不是悄悄退化成同世界。

**步骤**（174 默认是 cubesandbox，所以本用例要在本地或临时容器里做）：

```bash
# 本地：以 local 为默认后端起内存装配
python -c "
from tests.unit.test_production_composition import production_settings  # 仅示例
"
# 或在 174 上临时把某个 Agent 的部署 pin 到 local-development（需要 allow_unsafe_local_sandbox=true）
```

**判据**：
- `sandbox.provisioned` payload 为 `{"provider":"local","isolation":"workspace","enforcement":"none",...}`
- Cube 平台实例数不变（`sandboxes` 不增加）
- 事件序列里没有 `sandbox.logs`（只有失败才记）

**反例信号**：若 payload 出现 `isolation":"container"` 而 provider 是 local，说明形态标签错了（enforcement 推导 fail-closed 到 `none`）。

---

## B. 延迟代理 microVM（174 的默认形态）

**目的**：工具执行真的发生在 microVM 里，且租约/信任字段齐全。

**步骤**：

```bash
SID=$(mk_session '"agent_version":"0.1.0"')      # 不带环境 → 默认后端
RID=$(mk_run "$SID" '"用 Bash 工具写入 case-b.txt，内容为 b，然后回复一句话"')
wait_run "$RID"
provisioned "$RID"
```

**判据**：
- payload：`provider=cubesandbox-deferred`、`isolation=container`、`enforcement=delegated`、`trust_watermark/trust_floor/trust_floor_met`、`lease_id/lease_epoch`
- 事件里出现 `tool.request → tool.allowed → tool.result`、`workspace.archived`、`run.succeeded`
- 结束后平台实例数回到基线（`destroy` 策略；`sandboxes=0` 或与运行前一致）

**失败时的补充信号**：`run.failed` 前应有 `sandbox.logs`（见用例 I）。

---

## C. 出口策略形态：按 Agent 灰度（本会话已验证过）

**目的**：同一 Agent、同一 prompt，两个环境的出口行为相反——证明策略来自**部署快照钉住的 profile**，而不是全局开关。

**步骤**：

```bash
PROMPT='"用 Bash 执行：python3 -c \"import socket,urllib.request; print(\\\"dns=\\\", socket.gethostbyname(\\\"pypi.org\\\")); print(\\\"http=\\\", urllib.request.urlopen(\\\"https://pypi.org\\\", timeout=8).status)\""'

# 对照：不带环境（默认后端，declared）
S1=$(mk_session '"agent_version":"0.1.0"'); R1=$(mk_run "$S1" "$PROMPT"); wait_run "$R1"
# 实验：test 环境（快照钉 cubesandbox-egress-enforced）
S2=$(mk_session '"environment":"test"'); R2=$(mk_run "$S2" "$PROMPT"); wait_run "$R2"
```

**判据**：

| 会话 | 期望工具输出 |
| --- | --- |
| `$R1`（declared） | `dns=151.101.x.x`、`http=200` |
| `$R2`（enforced） | `Temporary failure in name resolution`（或 `gaierror`），HTTP 失败 |

结构侧佐证：平台实例的 metadata 里，enforced 实例带 `harness.egress=<指纹>`，declared 实例没有；两者的 `harness.tenant/session/run` 都在。

**回滚**：无需回滚（只读行为）；测试对象见第 10 节。

---

## D. 出口策略形态：全局开关

**目的**：确认"全局 enforced"能让**没有钉 profile** 的 Run 也受控（本会话 A/B 的另一半）。

**步骤**（改 env + 重建，可回滚）：

```bash
cd /data/merge-develop-20260918
# 1) 备份
cp compose.api.release.private.json compose.api.release.no-egress.model.json

# 2) 给 api + worker 注入全局开关（JSON 就地改，用镜像里的 python）
docker run --rm --user root -v /data/merge-develop-20260918:/d --entrypoint python \
  kai/axis-api:merge-develop-20260918 -c "
import json
d=json.load(open('/d/compose.api.release.private.json'))
for n in ('api','worker'):
    d['services'][n].setdefault('environment',{})['HARNESS_SANDBOX_EGRESS_ENFORCEMENT']='enforced'
json.dump(d, open('/d/compose.api.release.egress-on.json','w'), indent=2)"

# 3) 重建
docker compose -p agent-studio-174 -f compose.api.release.egress-on.json \
  up -d --no-deps --force-recreate --scale worker=3 api worker
```

**判据**：用例 C 里的**对照**会话（不带环境）现在也变成 DNS 拒绝；把 override 去掉重建后恢复 200。

**回滚**：用 `compose.api.release.private.json` 重建（原 tag 不变）。

---

## E. 常暖跨轮复用（keep_warm）

**目的**：同一会话的连续两轮复用同一个 microVM 实例（省掉创建开销），而不是每轮新建。

**步骤**：

```bash
# 1) 把 idle 策略切成 keep_warm（同用例 D 的注入方式，键名 HARNESS_CUBESANDBOX_IDLE_POLICY=keep_warm），重建 api+worker
# 2) 同一会话连跑两轮
SID=$(mk_session '"agent_version":"0.1.0"')
R1=$(mk_run "$SID" '"用 Bash 写入 warm-1.txt"'); wait_run "$R1"
R2=$(mk_run "$SID" '"用 Bash 写入 warm-2.txt"'); wait_run "$R2"
# 3) 看复用日志与实例元数据
docker logs --since 10m agent-studio-174-worker-1 2>&1 | grep -E "reusing warm sandbox|discarding unhealthy" | tail -5
ls_sandboxes      # 期望：第二轮结束后仍有一个实例，且 harness.keep=keep_warm、harness.session 与两轮一致
```

**判据**：
- worker 日志出现 `reusing warm sandbox sandbox_id=<同一 id>`
- 第二轮结束后实例仍在（`harness.keep=keep_warm`），且 `harness.session` 与两轮一致
- 第二轮的工具输出里，上一轮的 `warm-1.txt` **不存在**（复用时会清掉上一个 Run 的 workspace，防止污染）

**反例信号**：若日志是 `discarding unhealthy warm sandbox`，说明 `ping()` 失败——复用是缓存语义，会新建实例，不算通过。

---

## F. pause/resume（进程态跨轮存活）

**目的**：Run 结束后实例被**暂停**（释放宿主），下一轮恢复同一实例且**进程状态延续**。

**步骤**：

```bash
# 1) 注入 HARNESS_CUBESANDBOX_IDLE_POLICY=pause，重建 api+worker
# 2) 第一轮：起一个后台计数器
SID=$(mk_session '"agent_version":"0.1.0"')
R1=$(mk_run "$SID" '"用 Bash 执行：nohup sh -c \"for i in \$(seq 1 1000); do echo \$i >> /tmp/counter.txt; sleep 1; done\" >/dev/null 2>&1 & echo started"')
wait_run "$R1"
# 3) 检查平台状态：该会话的实例应进入 paused（keep 标记 = pause）
ls_sandboxes
# 4) 第二轮：读计数器行数
R2=$(mk_run "$SID" '"用 Bash 执行：wc -l /tmp/counter.txt 2>/dev/null || echo missing"')
wait_run "$R2"
docker logs --since 10m agent-studio-174-worker-1 2>&1 | grep -E "reusing warm sandbox|failed to pause" | tail -3
```

**判据**：
- 第一轮结束后实例进入 paused；日志无 `failed to pause`
- 第二轮出现 `reusing warm sandbox`（复用同一个 `sandbox_id`）
- `/tmp/counter.txt` 行数 **> 第一轮结束时的行数**（暂停期间进程不推进，恢复后继续追加）——这是"进程态跨轮存活"与"新建实例"的决定性区别；新建实例时文件必然 `missing`

**已知不确定点（本用例要顺带定论）**：复用路径是 `attach()` + `ping()`。`CubeAsyncSandbox.connect` 对 paused 实例是否自动 resume，尚未在 174 端到端验证（此前只在 111 上单独验证过 pause/resume 与 snapshot 可用）。若日志出现 `discarding unhealthy warm sandbox`，说明 attach 没有唤醒 paused 实例 —— 这本身就是本用例要暴露的缺陷（需要在 `_reuse_warm_sandbox` 里显式 resume）。

---

## G. 远程 CLI 形态（remote_cli：CLI 跑在沙箱内）

**目的**：整条 CLI 数据面在沙箱内，而不是工具代理。

**步骤**（provider 级，绕开 API 与全局模式开关）：

```bash
# provider 级，绕开 API 与全局模式开关；cwd 必须是 /app 才能 import scripts 包
docker exec -w /app -i agent-studio-174-api-1 python - <<'PY'
import asyncio
from scripts.smoke_cubesandbox import smoke

asyncio.run(smoke(remote_cli=True, codex=False))
PY
```

**判据**：脚本打印创建/上传/执行/回收各阶段成功；CLI 二进制经离线上传并通过版本校验（`HARNESS_CUBESANDBOX_CLAUDE_CLI_VERSION=2.1.259`）；模型调用真实返回。

**对照（应被拒绝）**：把后端换成 opensandbox 时，`_runtime_sandbox` 会直接报配置错误（`HARNESS_SANDBOX_PROVIDER=opensandbox requires HARNESS_SANDBOX_EXECUTION_MODE=worker_cli_deferred`）——因为 OpenSandbox 的 execd 没有 stdin 半关闭，CLI 交互无法收尾（详见 `docs/opensandbox-remote-cli-gap-20260918.md`）。

---

## H. 多后端路由与"未启用即拒绝"

**目的**：执行 profile 钉住的后端必须在部署服务范围内；否则拒绝，而不是悄悄落到默认后端。

**步骤**：

```bash
# 1) 拒绝路径（无需改 env）：把某 Agent 的部署 pin 到 gvisor-production
#    在 Studio 里对该 Agent 建 production 环境部署，或直接用 API 建部署
#    期望：部署/预检失败并给出 execution_profile_sandbox_provider_not_enabled
# 2) 允许路径：给 api+worker 注入 HARNESS_SANDBOX_EXTRA_PROVIDERS=kubernetes（174 无 k8s 配置 → 启动即失败，
#    这正是"半配置的后端必须在启动期暴露"的行为）
```

**判据**：
- 未启用时：拒绝，且**没有**产生任何 cubesandbox 实例（错误的形态绝对不能被执行）
- 配置了 extra 但未配全（缺 `HARNESS_KUBERNETES_IMAGE` 等）：容器启动失败（fail fast），不是运行期才发现

---

## I. 观测形态：失败 Run 的平台日志

**目的**：Run 在沙箱侧失败时，事件里带有平台自己的日志（覆盖 execd 就绪前的窗口与销毁过程）。

**本轮实测结论（2026-09-18，已修复并复验）**：

- 首次执行发现**缺陷**：`sandbox_logs` 只实现在 E2B provider 上，而 174 部署形态持有的是延迟包装器 `DeferredToolSandboxProvider`，它没有转发该方法 → `_record_sandbox_logs` 直接 return，**部署形态下 `sandbox.logs` 永远不会产生**。A/B 证据（同一探针脚本、同一镜像族的两个 tag）：

  | 镜像 | 结果 |
  | --- | --- |
  | `kai/axis-api:merge-develop-20260918`（修复前） | `AttributeError: 'DeferredToolSandboxProvider' object has no attribute 'sandbox_logs'` |
  | `kai/axis-api:obslogs-20260918`（修复后，已部署） | 返回 5 行真实平台日志（含 `forward init log stdout start`、`cid tpl-f116a5f3…` 等） |

- 修复：`deferred.py` 增加 `sandbox_logs` / `sandbox_metrics` 透传（只读已存在的 `lease.remote`，绝不为了读日志去创建沙箱）；`e2b.py` 的 `list_managed` 对 SDK 解析不了的页改为结束本次扫描而不是抛出（Cube 在实例启动中会省 `endAt`，实测 `KeyError: 'endAt'`）。提交 `2800942`，新增 5 个单测，全量 1279 passed。
- **期望的判据要写对**：只有当失败发生在**实例仍存在**时才有日志可读；如果失败原因是实例被外部删除/平台 TTL 到期，日志源本身已消失，读到空是正确行为（`_record_sandbox_logs` 是 best-effort）。

**步骤**（引入"实例仍存活时的失败"才有效）：

```bash
SID=$(mk_session '"agent_version":"0.1.0"')
RID=$(mk_run "$SID" '"用 Bash 执行：sleep 600"')
sleep 15                      # 等实例起来、命令开始跑
ls_sandboxes                  # 从输出里取该会话（harness.session = $SID）的 sandbox_id
SANDBOX_ID=<上一步的 id>
# 让工具调用超时：不要删实例，只观察工具超时后 Run 失败（实例届时仍在，直到 Run 终结才被销毁）
wait_run "$RID"
```

**判据**：`run.failed` 之前有 `sandbox.logs`，`provider=cubesandbox-deferred`，`line_count>0`，`lines` 来自平台日志尾巴；读取发生在 `destroy` 之前。

**当前平台的事实**：Cube 不返回资源指标，`sandbox_metrics` 恒为 `None`（已实测），所以指标形态目前只有 `active_count()` 这一类计数可用。

**若没有 `sandbox.logs`**：确认失败原因是不是"实例已被删除/超时回收"（此时无日志可读属正常），或失败发生在 provision 之前（例如被策略拒绝，本就不该有该事件）。
events "$RID" | docker exec -i agent-studio-174-api-1 python -c '
import json,sys
for line in sys.stdin:
    e=json.loads(line)
    if e["type"] in {"sandbox.logs","run.failed"}:
        p=e["payload"]; print(e["type"], json.dumps(p,ensure_ascii=False)[:300])
'
```

**判据**：`run.failed` 之前有 `sandbox.logs`，`provider=cubesandbox`，`lines` 非空且来自平台日志尾巴；读取发生在 `destroy` 之前（每个 Run 只读一次）。

**若没有 `sandbox.logs`**：说明失败发生在 provision 之前（例如被策略拒绝），此时不应有该事件——这也是判据的一部分。

---

## J. 租约与孤儿回收

**正向（本轮已做，可复跑）**：

```bash
# 跑任意一轮后
docker exec -i agent-studio-174-postgres-1 psql -U harness -d harness -c \
"select lease_id, run_id, owner, epoch, provider, state, created_at, released_at
   from sandbox_leases order by created_at desc limit 3;"
```

**判据**：每个 Run 一行；`owner=run-fence:<fencing_token>`；`epoch=1`；Run 终态后 `state=released`、`released_at` 有值；`state='live'` 的行应该在 `sandbox_leases` + 平台实例清单里一一对应（无残留 live 行）。

**反向（回收无主实例）**：

```bash
# 人为制造孤儿：把某个 live 租约手工过期，让维护任务按 TTL 回收其实例
docker exec -i agent-studio-174-postgres-1 psql -U harness -d harness -c \
"update sandbox_leases set expires_at = now() - interval '5 minutes' where state='live';"
# 观察维护任务（reliability controller 的 sandbox-orphans / sandbox-expiry:<backend>）
docker logs --since 10m agent-studio-174-worker-1 2>&1 | grep -iE "sandbox-orphans|sandbox-expiry|reclaim" | tail -5
curl -sS -H "Authorization: Bearer $CUBE_KEY" http://172.20.109.111:13000/health   # sandboxes 应下降
```

**判据**：维护任务日志出现回收动作，平台实例数下降；带 `harness.keep` 标记的常暖实例**不被**回收（由平台 TTL 负责）。

---

## 10. 清理

每个用例都会留下 session/run（不产生新的常驻对象，除 E/F 会留下常暖/暂停实例）。清理：

```sql
-- 只读核对
select count(*) from sandbox_leases where state='live';
select lease_id, sandbox_id, state from sandbox_leases order by created_at desc limit 10;
```

- 常暖/暂停实例：跑完用例 E/F 后把 `HARNESS_CUBESANDBOX_IDLE_POLICY` 还原为 `destroy` 并重建；残留实例用平台 API 删除，或等平台 TTL。
- 探针 Agent / `test` 部署快照 / 目录里的 `agentModelBindings` 绑定：确认后清理（见 `docs/merge-develop-174-release-20260918.md` 第 7 节）。


---

## 11. 本轮实测汇总（2026-09-18，174）

| 用例 | 结果 | 关键证据 |
| --- | --- | --- |
| B 延迟代理 microVM | ✅ | `run_ee0b872c…` succeeded；`sandbox.provisioned` = `cubesandbox-deferred / container / delegated` + `lease_id/lease_epoch`；事件含 `tool.request/tool.result/workspace.archived` |
| C-对照 declared | ✅ | `run_13f4993b…` succeeded；工具输出 `dns= 151.101.64.223`、`http= 200` |
| C-实验 enforced | ✅ | `run_50483d67…` succeeded；工具输出 `Temporary failure in name resolution`（无 dns/http） |
| I 观测（修复前） | ❌→✅ | 修复前 `AttributeError: no attribute 'sandbox_logs'`；修复后同一探针返回 5 行平台日志；已部署 `kai/axis-api:obslogs-20260918` |
| J 租约 | ✅ | `live leases: 0`；最近 5 条租约全部 `released` 且 `released_at` 有值；平台 `sandboxes: 0`（无孤儿） |

未执行（会改变平台运行参数，需单独确认）：A（同世界，174 未装 local 后端）、D（全局出口开关）、E（keep_warm）、F（pause）、G（remote_cli provider 级）、H（多后端路由）。D/E/F 都是"改 env + 重建 api/worker"的一步操作，rollback 就是把环境变量去掉重建。
