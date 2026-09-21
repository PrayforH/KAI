# 174 CubeSandbox 执行与同会话保活修复（2026-09-21）

## 结论

174 的 API、3 个 Worker 已启用 CubeSandbox，新模板为 `tpl-fb4a185de6184b69a94b1842`。
执行模式为 `worker_cli_deferred`，空闲策略为 `keep_warm`，超时 3600 秒，关闭 `allow_unsafe_local_sandbox`。
纯聊天不创建远端实例；首次实际调用沙箱工具时创建，同租户、同会话的后续执行复用实例。
保活不是永久存储：实例超时或被回收后，`/tmp` 状态会丢失，正式产物仍应归档。
`pause` 本次未启用或验收。

## 根因

1. 后续部署将实际运行配置改回了 `sandbox_provider=local`、`sandbox_execution_mode=remote_cli`，Cube 模板为空，且允许本地执行。
   用户两轮验收 `run_5a8fa43dd34c4698800e6364ff6d961f`、`run_0a3d05f2b50f4e1aaf907dea2118c08d` 的事件均记录 `provider=local / enforcement=none`。
   读回 `/tmp` SQLite 数据仅证明本地 Worker 文件尚在，不能证明使用了 CubeSandbox。
2. 当前镜像 E2B SDK 已升级至 2.51.0，其 create/connect 工厂改用 `/v2/sandboxes` 和 `/v2/sandboxes/{id}/connect`。
   本套 Cube 创建返回 405，重连失败。因此只修改环境变量仍不能修复。

## 修改

- Cube 适配器显式调用 v1 创建和重连接口，不再依赖 E2B 工厂选择 API 版本。
- 保留 metadata、超时、网络限制和卷挂载，传递实例凭据及代理 Host；管理凭据不进入数据面。
- HTTP 创建失败直接抛出，不降级到本地执行。
- Compose 补充 idle policy、template validation 参数透传。
- 174 的 `.env.production` 和 `compose.deepagents-174.yaml` 均持久化 Cube 配置，私密配置不提交到 Git。

官方 Cube Python SDK 的创建/连接契约可参阅 [CubeSandbox SDK README](https://github.com/tencentcloud/cubesandbox/blob/master/sdk/python/README.md)。错误状态与版本切换均由本次实际镜像测试确认。

## 验收证据

| 测试 | 结果 |
|---|---|
| 协议/生命周期实测 | 创建、中文文件上传下载、stdout/stderr/exit code、超时中止后再次执行、销毁通过 |
| Provider 两轮复用 | 两轮均使用 `9d9518230dae49ee945170f9a7785b2c`，读回原 `/tmp` 校验值；测试后删除 |
| Claude SDK 主会话执行 | `run_43f0a3885eac4d9885ec6ea932003ae7`，事件为 `cubesandbox-deferred`，Python 实际执行，CSV 合计 60 |
| 主会话下载 | `cube-proof.csv` 27 字节，实际下载 SHA-256 与 artifact 记录一致：`1a0eaac8a6c35e5745cf174f9fbe8fce67a9deebaedc1eab732a1b2a7cdfb521` |
| Claude SDK 第二轮 | `run_4d5fc492a04142a8868704d5db579839`，读回 UUID `168612ba-6722-4ad8-8ec1-161f566b8bf1`，实例仍为 `327b95ff36ae4fa8a40eedc612ae96ed` |
| DeepAgents 纯聊天 | `run_924a0d4a15844227b3ebe235377ac707`，无工具请求、无 Cube 实例 |
| DeepAgents 首轮执行 | `run_8746c89e0e7e48b4857bce9d83e5ab86`，`runtime_tool_name=execute`，实例 `6b6489a6d82949e1865824c75255f945` |
| DeepAgents 第二轮 | `run_57887e8611764d15a220bef6699bc37a`，同实例读回文件中的 UUID `d3f01de3-0865-417a-b1a9-8b702eafc299` |
| 自动化回归 | Cube、E2B、deferred、DeepAgents backend 共 89 项通过；Ruff 与 diff whitespace 检查通过 |

上述主会话测试使用独立验收账号，DeepAgents 使用账号内专用验收智能体，不修改业务用户智能体。

## 发布与回滚

- API：`kai/axis-api:cube-v1-20260921`
- Worker：`kai/axis-worker:cube-v1-20260921`（3 副本）
- 基于当时实际 `webfetch-proxy-20260920` 镜像，仅替换 Cube 适配器，保留已有后端修改。
- API 和 3 个 Worker 均健康，逐容器读取 Settings 验证配置生效。
- 174 发布目录：`/data/cube-restore-20260921`，含旧 compose 和环境文件备份；回滚须同时恢复 `compose.deepagents-174.before.yaml` 与 `env.production.before.private`，再使用原 compose 文件组合更新 api/worker。
- Web 和 173 Worker 未重新发布。

## 可复测的两轮问题

第一轮：请实际执行 Python，在 `/tmp/cube-demo.txt` 写入随机 UUID，并输出该 UUID 和 `socket.gethostname()`。

第二轮（同一会话）：请实际执行 Python，读取 `/tmp/cube-demo.txt` 并输出内容，不要重新生成或改写文件。

判断依据是 Cube 平台按 `harness.session` 查到的实例 ID 和实际工具结果；仅看助手回复或 `sandbox.provisioned` 的 deferred 标记不能证明已经创建实例。
