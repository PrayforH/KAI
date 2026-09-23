# OpenSandbox@115 实测：查看与接入（2026-09-22）

用**仓库里已有的沙箱代码**对 `172.20.109.115` 做了一次查看 + 接入验证，验证前面那份
[三方 provider 对照](2026-09-22-sandbox-provider-cross-project-reconciliation.md) 的三条建议在这个
部署上成立到什么程度。全部为实测；失败与假象都写在文里。

登录方式：`ubuntu@172.20.109.115`，密码 **含结尾 `$`**（`Qwer@115$`），`sshpass -e` 传。
服务面 `http://172.20.109.115:8090`，`OPEN-SANDBOX-API-KEY` 头鉴权（key 从 `opensandbox-mcp`
容器的 env 取；`/data/opensandbox/.env` 与 config.toml 是 root 600，`ubuntu` 无免密 sudo，
但 `docker` 免 sudo 可用）。

## 1. 环境事实

| 项 | 值 |
|---|---|
| 主机 | `ubuntu-115`，Ubuntu 22.04.5，125 GiB 内存，已运行 249 天 |
| 服务 | `opensandbox-server v0.2.3`（容器 `opensandbox-server`，`opensandbox/server:v0.2.3`，8090）＋ `opensandbox-mcp 0.1.1`（8000） |
| 其他容器 | hugegraph-hubble / hugegraph-server / nebula-graphd·metad·storaged0 / nebula-studio（与本任务无关） |
| docker 运行时 | `runsc` 已注册，默认运行时 `runc` |
| config.toml 位置 | 宿主 `/data/opensandbox/config/config.toml`，容器内 `/etc/opensandbox/config.toml`（`docker exec opensandbox-server cat` 可读） |

config.toml 关键项（与 sapling 那份的**差距正是我们这条线的价值**）：

```toml
[server]  max_sandbox_timeout_seconds = 86400
[runtime] type = "docker" ; execd_image = "opensandbox/execd:v1.1.0"
[secure_runtime] type = "gvisor" ; docker_runtime = "runsc"     # sapling 那份没有这一段
[docker]  network_mode = "opensandbox_runtime" ; host_ip = "172.20.109.115"
          port_range_min/max = 40000/40999 ; pids_limit = 4096
          drop_capabilities = [9 项] ; no_new_privileges = true
[ingress] mode = "direct"
[egress]  image = "opensandbox/egress:v1.1.7" ; mode = "dns"
[renew_intent] enabled = false
[store]   type = "sqlite"
```

**沙箱容器实测**（`docker inspect sandbox-<api id>`）：容器名是 `sandbox-<API 返回的 id>`，
`runtime=runsc`，容器内 `uname -r` = **`4.19.0-gvisor`**，capabilities 裁掉 9 项、
`no-new-privileges:true`、`pids_limit=4096`、网络 `opensandbox_runtime`、rootfs 可写。
→ **我们给 opensandbox 声明的 `delegated` 档位在 115 上有内核事实支撑**，不是纸面推断。

## 2. 接入结果（用已有代码，未改一行源码）

| 步骤 | 结果 |
|---|---|
| `scripts/smoke_opensandbox.py --only deferred` | **passed**（4.02s）：懒分配、上传、execute、collect、销毁 |
| `scripts/smoke_opensandbox.py`（full） | **失败**——`smoke()` 里 `assert handle.runtime_transport_factory is None`（"deferred-mode only"） |
| `scripts/smoke_opensandbox.py --only remote-cli` | **失败**——沙箱内 `curl: command not found` |

两处失败都不是 115 的问题，而是**契约陈旧**与**宿主平台差异**：

1. **契约不一致（两处）**：provider 自 `ecf107d3`（"carry the OpenSandbox remote CLI"）起
   **无条件**给每个 handle 挂 `runtime_transport_factory`（`opensandbox.py:692`），
   `prepare` 在非 deferred 时还会往沙箱装 Claude CLI（`:695-699`）；
   但 `composition.py:490-496` **仍然拒绝** `opensandbox` + `remote_cli`，理由是
   "execd 只有命令面与文件面，没有双向 CLI 传输"。同一份代码里两处说法相反，
   `scripts/smoke_opensandbox.py:198` 的断言站在旧的那一边。
2. **`python:3.12-slim` 没有 curl**：`_ensure_binary`（`opensandbox.py:220-247`）在非 Linux 宿主
   上必然回退到官方安装器（`_is_linux_elf(bundled)` 为 false），而安装器要 curl。
   macOS 开发机因此**无法**验证 CLI 安装路径；生产 Linux Worker 走 `_upload_binary`
   上传自带 ELF，不受影响。要在这台机器上验，镜像需自带 `curl`/`node`。

## 3. 探针实测（`work/probe_os115*.py`，全部用 provider 自己的代码）

### 3.1 文件面：**7/7 字节保真**

| 用例 | 结果 |
|---|---|
| `中文 名字 带空格.txt`（内容含 `'`、反引号、`$`、`;`、换行） | round_trip=true，sha 一致 |
| `quote's "name".txt` | true |
| `tab\tname.txt` | true |
| `binary.bin`（256 字节全值 0x00–0xFF） | true |
| `crlf.txt` | true |
| `empty.txt`（0 字节） | true |
| `big-3MiB.bin`（随机 3 MiB） | true |

并且**两个平面是同一棵树**：文件面写入的文件命令面 `cat` 得到 `from-file-plane`；
命令面 `printf >` 写出的文件文件面 `download` 得到 `from-command-plane`。
→ 建议 2 的前提成立：原生写入不会引入第二套命名空间。

### 3.2 PTY：经 115 的 server proxy **双向可达**

`remote_session()` → `start(["cat"])` → 写 `hello-through-pty` → 读回同样内容。
即 `POST /pty` + `ws://…/proxy/{port}/pty/{id}/ws?pty=0` 在 115 上**通**。
→ 09-17 文档里"待办 #1：验证经 115 ingress 的 WS 可达性与双向语义"**已完成**；
现在挡住 `remote_cli` 的只剩 composition 那道门（与镜像里的 CLI 依赖）。

### 3.3 生命周期：renew / pause / resume / snapshot

| 能力 | 实测 |
|---|---|
| `POST /renew-expiration {expiresAt}` | 200，返回同一时间戳；GET 复核确实被改成请求值 |
| `POST /pause` | 202 → 状态 `Paused`（`CONTAINER_PAUSED`） |
| `POST /resume` | 202 → 状态 `Running`；execd `/ping` **200**（数据面存活） |
| `POST /snapshots` | 202 → 约 5s 后 `Ready`（`snapshot_runtime_ready`，"Docker snapshot image created successfully"） |
| `GET /pools` | **501 `KUBERNETES::POOL_NOT_SUPPORTED`** |

两条要注意：

- **renew 是"设置绝对过期时间"，不是"延长"**。我第一次把 `now+45min` 传进去，
  而沙箱创建时按 `timeout=3600` 已到 `+60min`，结果**过期时间被改短了 15 分钟**。
  调用方必须自己保证单调递增，否则续期反而提前回收。
- **pool 在 115 不可用**：报错原文 "Pool management is only available when runtime.type is
  'kubernetes'."。所以"平台原生 warm pool"这条路在 docker 运行时上不存在。
- 快照第一次报 `snapshot_runtime_failed`（docker commit 500）——那是**我自己的时序假象**：
  我在发出快照请求后立刻销毁了沙箱。按 5 秒粒度复验后是 `Ready`，所以快照可用。

### 3.4 出口：**没有 DNS，不能下发策略**

沙箱内 `/etc/resolv.conf` 是 Docker 的 `nameserver 127.0.0.11`，但
`harbor.shdata.com` / `pypi.org` / `claude.ai` **全部 gaierror**；按 IP 可达（115:8090 → 200）。
更关键的是下发 `networkPolicy` 会被直接拒绝：

```
HTTP 400 SANDBOX::INVALID_PARAMETER
networkPolicy is not supported when docker network_mode='opensandbox_runtime'
(user-defined network). Use network_mode='bridge' to enable network policy
```

由此三条结论：

1. 115 上的沙箱**只能按 IP 访问可达网段**，没有域名解析、没有公网。任何依赖 hostname 的
   东西（`pip install`、官方 CLI 安装器、按域名连模型网关）在 115 上都不成立。
2. 我们 provider 在 `HARNESS_OPENSANDBOX_ALLOW_INTERNET_ACCESS=false` 时下发
   `{"defaultAction":"deny","egress":[]}`（`opensandbox.py:829-833`），在 115 上会**创建即失败**
   （同一 400 形状）。当前默认 `true` 才没暴露。这是我们这边要处理的兼容点。
3. 真要 egress（含 DNS），得把服务端 `[docker] network_mode` 改成 `bridge`
   ——那是 115 的部署决策，不是客户端能绕的。

### 3.3.1 TTL / pause / renew 三组对照（决定了"常暖该靠什么"）

三个沙箱同时建，`timeout=60`（`expiresAt` 都是 t0+60s）：

| 组 | 处理 | t=90s | t=120s |
|---|---|---|---|
| A 自然过期 | 无 | **GONE(404)** | GONE |
| B 暂停 | t≈0 `POST /pause` → 202 | **GONE(404)** | GONE |
| C 续期 | t=45s `renew-expiration` → `2030-01-01` | `Running`，expires=2030-01-01 | 同左 |

三条结论：

1. **过期即删除**：TTL 到点容器就没了（A 直接 404），工作区随之消失。所以"沙箱是缓存"必须
   有 collect 在每轮结束时把工作区收回来，否则丢的是数据。
2. **pause 不冻结 TTL**：B 暂停后**照样**在原 `expiresAt` 被回收。pause 只是"释放 CPU/内存但
   保留容器文件系统"的**成本**杠杆，**不能**用来把沙箱停放起来等下一次会话。
   推论：`resume` 只是因为用了 `pause` 才存在——不 pause 就永远不需要 resume。
3. **renew 是唯一能跨过原 TTL 的手段**：C 在原过期点之后仍然 `Running`。
   但注意 **续期不受 `max_sandbox_timeout_seconds` 约束**：该上限（115 是 86400s）只管创建，
   `renew` 接受了 `2030-01-01` 这种远未来值。加上 §3.3 那条"renew 是设置绝对值、能改短"，
   我们若实现续期，**必须自己夹上限 + 保证单调递增**；否则 `expiresAt` 不再是孤儿兜底，
   一个 bug 就能把沙箱挂几年。

## 4. 厂商 SDK 评估（建议 1）

`opensandbox 0.1.16`（与 sapling 同版本）实测可装（`uv pip install --target`），
依赖 `attrs / httpx / httpx-sse / pydantic / python-dateutil`（pool-redis 为 extra）。

| 我们手写的坑 | SDK 是否覆盖 |
|---|---|
| SSE 是空行分隔的裸 JSON 行 | 覆盖：`adapters/command_adapter.py`（`Accept: text/event-stream`）、`execution_event_dispatcher.py` |
| 退出码在 `error` 事件、`evalue` | 覆盖：`api/execd/models/server_stream_event_error.py`、`adapters/converter/event_node.py:29`（`evalue`） |
| multipart 经 server proxy 必须带 `Content-Length` | 覆盖：`adapters/filesystem_adapter.py:250-282` 注释原文 "server proxy, which does not support chunked multipart" |
| **PTY / WebSocket 会话** | **不覆盖**：全包 grep `pty` 无命中，依赖里没有 websockets |
| pool（warm pool） | 有 `pool*.py` + redis store + reconciler，但**是 kubernetes 运行时的能力**，115 上 501 |

→ SDK 可以替掉 `OpenSandboxRemoteSandbox` 里的**命令面与文件面协议解析**，
但**替不掉 `opensandbox_session.py`（354 行 PTY）**。也就是说"用 SDK 当传输"这条能省的是
`opensandbox.py` 里协议解析那一段，PTY 面必须自留。不要因为"sapling 用了 SDK"就以为能整段替换。

## 5. 三条建议在 115 上的落点

1. **SDK 当传输**：范围收窄为"命令面 + 文件面"，判据是替换后
   `opensandbox_session.py` 零改动、`tests/unit/sandbox/test_opensandbox.py` 里的协议解析用例仍过。
2. **文件原语**：前提已验证（字节保真 + 同树）。真正收益仍是去掉
   `deferred.py:172-192` 的命令形状推断。唯一新增的部署约束来自 §3.4——策略与 egress 在这种
   `network_mode` 下不可用，文件面本身不受影响。
3. **常暖**：**115 上不要选 pool 路线**（501）。可用组合是
   **确定性命名 + `renew-expiration` + 我们自己的租约**，需要时再叠 `pause`/`resume`（都实测可用）。
   两点缺口在我们这边：`OpenSandboxClient` 目前**只有 create / 状态查询 / 销毁，没有续期与
   pause/resume 方法**；且服务端 `[renew_intent] enabled = false`，说明平台不会替我们续期。
   续期接口若是我们实现，**必须做单调延长**（见 §3.3 的踩坑）。

## 6. 未做与未验

- 未在 115 上跑通 `remote_cli` 的完整 SDK 回合：被镜像缺 curl 挡住（§2.2）。要验需换
  `curl`/`node` 齐备的镜像，或从 Linux Worker 走 ELF 上传路径。
- 未测真实模型调用的网关连通性（`HARNESS_OPENSANDBOX_SMOKE_*` 未提供）。
- 未改任何源码：本文只记录"已有代码在 115 上跑成什么样"。两处契约不一致（§2.1）留给决策。
- 探针脚本在 `work/probe_os115*.py`（`work/` 已 gitignore），未提交；`work/opensandbox-115.env`
  含 115 的 API key，同样仅在本地。
- 收尾：跑完 `/v1/sandboxes` 为空、快照已清理，115 上无本任务残留。
