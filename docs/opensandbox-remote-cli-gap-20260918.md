# OpenSandbox remote_cli 缺口：execd 会话缺 stdin 半关闭

日期：2026-09-18。结论来自对 115 部署（OpenSandbox server v0.2.3 / execd v1.1.0）的实测，
以及与 `components/execd` 源码的逐条核对。本文只记录事实与选型依据，未改动平台代码。

## 背景

`worker_cli_deferred` 模式已实现并实测通过（`src/harness/sandbox/opensandbox.py`，见
`docs/` 中 provider 相关记录）。下一阶段是 `remote_cli`：把 Claude/Codex CLI 放进沙箱，
由平台的 `RemoteClaudeSession` 契约驱动。该契约要求：

```python
start(argv, cwd, env) / write(data) / end_input() / read_stdout() / read_stderr() / wait() / terminate()
```

其中 `end_input()` 必须真正**关闭子进程的 stdin**。远端命令以
`--input-format stream-json` 启动（`src/harness/runtime/daytona_transport.py:98`），
CLI 需要 stdin EOF 才会结束本轮并退出；否则 `read_messages()` 等不到 stdout EOF，Run 挂到超时。

## execd 侧可用的通道（实测）

唯一的 stdin 通道是 PTY/WS 会话（`POST /pty` + `GET /pty/{id}/ws`）。`/command` 无 stdin 字段。

在 115 上用 `?pty=0` 请求 pipe 模式，实测结果：

| 能力 | 结果 |
| --- | --- |
| 首帧 | `{"type":"connected","session_id":"…","mode":"pipe","role":"holder"}` |
| stdout / stderr | **分离且为原始字节**：`stdout=b'out1\n'`、`stderr=b'err1\n'`（无终端回显、无 CRLF 转换） |
| 进程退出 | 下发 `{"type":"exit","exit_code":5}`，随后 WS 以 code=1000 `process exited` 关闭 |
| stdin 写入 | 二进制帧 `[0x00][原始字节]` 生效（`cat` 原样回显） |
| 长驻进程 | 不退出则无终帧（`cat` 实测超时无输出） |
| 会话状态 | `GET /pty/{id}` → `{session_id, running, output_offset}` |
| 增量输出 | `/command/{id}/logs?cursor=` 返回 `EXECD-COMMANDS-TAIL-CURSOR`；`/command/status/{id}` 给 `exit_code` |
| WS 可达性 | **经生命周期 API 服务端代理即可**（`/v1/sandboxes/{id}/proxy/44772/pty/{id}/ws`），无需直连沙箱端口 |

代码依据：`components/execd/pkg/web/controller/pty_ws.go:135`（`pipeMode := ctx.Query("pty") == "0"`）、
`:244-251`（`connected` 帧与 mode）、`:637`（`exit` 帧带 `exit_code`）、
`components/execd/pkg/runtime/pty_session.go:302`（`StartPipe`：`cmd.Stdout`/`cmd.Stderr` 为独立 os.Pipe）、
`model/pty_ws.go:40-43`（二进制帧类型字节）。

## 缺口

**execd 没有 stdin 半关闭（EOF）机制。** 实测：发送零长度二进制帧后 `cat` 依然存活；
`ClientFrame` 只有 `type/data/cols/rows/signal` 字段，无 close/eof 语义；WS 层也没有
half-close 的表达。结束会话只能靠 `{"type":"signal"}` 杀进程或 `DELETE /pty/{id}`。

因此 `end_input()` 无法在现有 execd 通道上实现。

## 三条路径

| 路径 | 做法 | 代价 | 语义 |
| --- | --- | --- | --- |
| A. 沙箱内桥接服务 | 上传一个小进程，持有 CLI stdin 并自行实现 close；stdout/stderr/exit 经自身端口（走 lifecycle proxy，已实测可达）暴露 | 新增沙箱内组件：协议、端口暴露、镜像/上传治理、安全评审 | **真 EOF、真退出码**，与现有契约一一对应 |
| B. 传输层按 result 终止 | 收到流式 `result` 事件后主动终止会话，不等 EOF | 改动**共享**传输层（Daytona/E2B/K8s 共用），需 provider 能力开关 | 退出码来自信号杀死，须特判"已成功"以免误报失败 |
| C. 改用打印模式 | 远端命令改为 `-p` + 内联 prompt，不需要 stdin | 改动共享的命令构造与输入路径 | 会话续跑与逐轮交互语义随之变化 |

选型文档（`docs/sandbox-selection-and-evolution-20260915.md` §5.1）已把这情形列入：
优先验证无终端语义的字节流（本文件证实 pipe 模式满足），"确有缺口再在受控镜像内增加
轻量进程桥接服务"——即路径 A。

## 当前状态与纪律

- 现状**未静默降级**：`remote_cli` + `opensandbox` 在 `src/harness/composition.py:374-381`
  显式拒绝，而不是把 CLI 留在 Worker 冒充远端执行。
- 受影响的范围：任何被判定需要 `remote_cli` 的 agent（含自包含 Python 工具的 bundle、
  非只读外部 MCP）目前不能用 OpenSandbox 后端，只能用 CubeSandbox/Daytona/E2B。
- 未决问题：路径 A 是否接受在受控镜像内新增一个组件；这是架构决策，需要明确取舍后再实施。
