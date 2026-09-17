# OpenSandbox 作为 SandboxProvider 接入（2026-09-17）

范围：把 115 上已修复的 OpenSandbox（server `0.2.3`，gVisor `runsc` 运行时）按
`SandboxProvider` 契约接入 Harness，替代「MCP 工具形式」的临时用法。本文记录
一手探测结论、实现取舍、真实验证结果与后续待办。

## 1. 一手探测结论（2026-09-17，115）

服务面（`http://172.20.109.115:8090`，`OPEN-SANDBOX-API-KEY` 头鉴权，`/health`、`/version` 开放）：

| 能力 | 端点 | 结论 |
|---|---|---|
| 创建 | `POST /v1/sandboxes` | 返回 202 + `{id,status{state},expiresAt}`；实测创建 **0.8~1.4s** 且创建即 `Running` |
| 查询/销毁 | `GET`/`DELETE /v1/sandboxes/{id}` | 204；销毁后 `GET` 404（孤儿检查依据） |
| 续期/暂停/快照 | `renew-expiration`、`pause`、`resume`、`snapshots` | 存在，L2/L3 档位的前置能力 |
| execd 代理 | `/v1/sandboxes/{id}/proxy/44772/**` | 可用；Worker 只需一个可达端点，无需解析每沙箱 ingress 端口 |

创建请求要点：`image` 与 `entrypoint` **必须同时给**（只给 image 报 422）；`entrypoint`
被平台包装为 `/opt/opensandbox/bootstrap.sh`，execd 由平台从 `opensandbox/execd:v1.1.0`
注入到 `/opt/opensandbox/execd` 并拉起（实测 PID 1 是 bootstrap，execd 是其子进程）。
只给 `entrypoint: ["tail","-f","/dev/null"]` 时容器内没有 execd —— 这一点此前被误判为
「execd 未注入」，实际是入口脚本未拉起，需在创建后等 `/ping` 就绪（本实现内置等待）。

execd 数据面（`44772`，**无自身鉴权**，可达性即权限）：

| 能力 | 端点 | 要点 |
|---|---|---|
| 就绪 | `GET /ping` | 创建后约 1s 可用 |
| 执行 | `POST /command` | `text/event-stream`，但 body 是**裸 JSON 行**（以空行分隔，无 `data:` 前缀）；同时兼容带前缀的旧构建 |
| 文件 | `/files/upload`（multipart）、`/files/download?path=`、`/directories/list?path=&depth=`、`POST /directories` | 上传经 server proxy 时必须带 `Content-Length`（不支持 chunked multipart） |

执行语义（实测，决定了实现细节）：

- **不支持 `argv`**：`0.2.3` 的 execd 校验 `command` 必填，`argv` 被拒。因此 `execute(argv)`
  用 `shlex.join` 拼成一条命令行提交。
- **退出码在 `error` 事件里**：`{"type":"error","error":{"ename":"CommandExecError","evalue":"7",
  "traceback":["exit status 7"]}}`；超时被杀是 `evalue:"-1"` + `signal: killed`。没有
  `execution_complete` 携带状态码，流结束即命令结束。
- **输出按行分帧**：每个输出行一个事件，**行终止符被去掉**；`\n` 与 `\r` 都算行分隔；空行以
  精确文本 `"\n"` 出现。因此重建文本按行 join（`"\n"` 事件还原为空行），最后一行的终止符
  不可恢复，选择不臆造。
- **stdout/stderr 会分组到达**（不保证交叉顺序），但两路各自有序、可分别重建。
- 大输出（实测 100KB 单行）作为单事件返回，不分块。

## 2. 实现取舍

- 新增 `src/harness/sandbox/opensandbox.py`：`OpenSandboxClient`（生命周期 + execd 句柄）、
  `OpenSandboxRemoteSandbox`（命令与文件）、`OpenSandboxSandboxProvider`（Run 级契约）。
- **只走 server proxy**：所有 execd 调用经 `/v1/sandboxes/{id}/proxy/44772`，不依赖
  `[ingress] mode="direct"` 下的每沙箱端口；API key 只留在 API 侧，不进入沙箱。
- **上传批量**：32 文件 / 8MiB 一批，避免 multipart 请求过长与逐文件往返。
- **生命周期客户端懒建**：装配 provider（每次进程启动都会发生）不建立连接、不泄漏空闲
  `AsyncClient`；这同时也是本地测试环境变量污染的隔离点。
- **失败关闭**：`evalue` 非数字时抛 `OpenSandboxCommandError`，不降级为 exit 1；销毁后再查
  生命周期 API 应 404（烟雾脚本即断言此点）。
- **enforcement 事实**：`opensandbox` → `delegated`。gVisor 内核边界由 provider 侧保证，平台
  只选择镜像与出口策略；这与 cubesandbox/e2b 同档，因此目录新增 profile
  `opensandbox-gvisor`（`minimumEnforcement: delegated`）。
- **只支持 deferred 模式**：execd 提供命令面与文件面，**没有 stdin 通道**。全量远程 CLI 需要
  双向进程传输，`0.2.3` 上唯一的通路是 execd 的 WebSocket PTY（`POST /pty` +
  `ws://…/pty/{id}/ws?pty=0`，pipe 模式下 stdin 为 `0x00`+原始字节）。在实现并验证该传输前，
  `HARNESS_SANDBOX_PROVIDER=opensandbox` + `remote_cli` 组合**拒绝启动**，而不是让模型进程
  悄悄留在 Worker 里。

## 3. 真实验证（2026-09-17，115）

`scripts/smoke_opensandbox.py`（创建→上传中文文件→执行→回收→超时恢复→销毁→孤儿检查，
再走一遍 deferred 包装路径）：

```
{"stage": "created", "seconds": 1.31}
{"stage": "tools_and_artifact_passed"}
{"stage": "timeout_passed", "killed_exit_code": -1}
{"stage": "deleted"}
{"stage": "sandbox_removed"}
{"status": "passed", "seconds": 7.57}
{"stage": "deferred_passed", "seconds": 3.53}
```

覆盖：中文文件名上传/回收一致、exit 7 + stdout/stderr 分离、超时被杀（-1）后沙箱仍可用、
销毁后生命周期 API 404、以及 deferred 包装的懒分配与脏工作区回收。验证后 115 上
`/v1/sandboxes` 为空，无孤儿实例。

本地：新增单测 13 项（协议解析、退出码映射、批量上传、回收上限与越界拒绝、配置校验、
组合拒绝/包装），沙箱包 65 passed；全量单测与 Ruff 通过（存量 E501 与本轮无关）。

## 4. 配置与部署

```
HARNESS_SANDBOX_PROVIDER=opensandbox
HARNESS_SANDBOX_EXECUTION_MODE=worker_cli_deferred
HARNESS_OPENSANDBOX_API_URL=http://172.20.109.115:8090
HARNESS_OPENSANDBOX_API_KEY=<部署私有 env>
HARNESS_OPENSANDBOX_IMAGE=python:3.12-slim   # 必须自带 python3 与 bash
```

镜像必须自带 `python3`（Bash/Read/Write/Edit 工具在沙箱内以 `python3 -c` 执行）与 `bash`；
`HARNESS_OPENSANDBOX_ALLOW_INTERNET_ACCESS=false` 时创建会附带
`networkPolicy={defaultAction: deny, egress: []}`（115 的运行配置注明 gVisor 下不建议提交
networkPolicy，默认保持 `true` 即不下发策略）。

### 4.1 174 发布记录（2026-09-17）

- 发布目录 `/data/opensandbox-20260917-api/`：`Dockerfile`（`FROM kai/axis-api:timeline-total-20260917`
  + `COPY files/harness ${SITE}` + 清理 `__pycache__`）、`files/harness/**`（feature 分支 HEAD 的
  整包覆盖，5.0MB / 570 个 .py）、`compose.api.release.private.json`（api+worker →
  `kai/axis-api:opensandbox-20260917`）、`compose.api.rollback.private.json`（→ `timeline-total-20260917`）。
- 切换前：活动任务 0（runs 全部终态）；`deployment_snapshots` 为空，因此新增的
  enforcement 下限门禁在当前 174 上没有可触发的输入。
- 切换后：api + 3 worker 全部 healthy，`/healthz` 200，控制台 `GET /v1/agui/threads` 200。
- 真任务级验证（新线程，经正式 API 入队，`public-opinion-agent` 0.3.13）：
  run `run_356da1ffa9a643258bfd67ae890fdd8a` **succeeded**；事件含
  `sandbox.provisioned` = `{"provider":"cubesandbox-deferred","isolation":"container","enforcement":"delegated"}`
  （生产后端仍为 CubeSandbox，enforcement 事实由本轮实现上报）、`tool.request/allowed/result`、
  `workspace.archived`、`run.succeeded`。
- 回滚：确认无活动任务后
  `docker compose -p agent-studio-174 -f compose.api.rollback.private.json up -d --no-deps --force-recreate --wait --scale worker=3 api worker`。
  本轮无数据库迁移。

## 5. 后续待办

| 序 | 事项 | 说明 |
|---|---|---|
| 1 | execd WebSocket PTY 传输 | 全量远程 CLI 的必经之路；已确认 API 形态（`/pty` + `pipe` 模式 stdin），需验证经 115 ingress 的 WS 可达性与双向语义 |
| 2 | 网络策略下发与回读 | 对应治理计划 P1-2；OpenSandbox 的 `networkPolicy` 是平台级下发点，比 cube-egress 更直接 |
| 3 | 耐久租约（P1-1） | provider 已带 `metadata{harness.tenant/session/run}`，可作为租约与孤儿回收的索引 |
| 4 | pause/resume 与池化（L2/L3） | 生命周期 API 已具备；依赖 P1-1 |
| 5 | Profile 级多后端路由（P1-4） | 目前仍是全局单选 `HARNESS_SANDBOX_PROVIDER`；OpenSandbox 的加入不改变该结论 |

明确不做：在没有 WS 传输前放开 `remote_cli`；把 OpenSandbox 设为默认后端（174 生产仍是
CubeSandbox）。
