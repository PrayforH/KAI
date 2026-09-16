# CubeSandbox 接入与 174 启用记录

## 结果

2026-09-15 已在 `172.20.109.174` 正式启用 CubeSandbox。API、3 个 Worker 与 quality-sync 使用新镜像，正式 Web 保持原版本。网页 `http://172.20.109.174:3501` 和 API `/healthz` 均返回 200。

| 项目 | 生效配置 |
| --- | --- |
| Provider | `cubesandbox` |
| 执行模式 | `worker_cli_deferred` |
| Studio 执行配置 | `cubesandbox-private` |
| Cube API | `http://172.20.109.111:13000` |
| Proxy | `http://172.20.109.111:80` |
| Sandbox domain | `cube.app` |
| Template | `tpl-f116a5f3d1c442b2b1690f4d` / `nexau-code` |
| Claude CLI | `2.1.259`，与现有 Worker 内置二进制一致 |
| Codex CLI | `0.149.0` |
| 新镜像 | `kai/axis-api:cubesandbox-20260915-144135` |
| 镜像 ID | `sha256:22655044c8a8536a2d0a43818e54cbdb2a9a9e82979eb8be9eddb5eee7b90797` |
| 发布目录 | `/data/cubesandbox-20260915-144135`（174 主机） |
| 原镜像 | `kai/axis-api:hitl-stream-20260915-134934` |

凭据保存在私有环境文件中，权限为 `0600`，未写入版本控制。发布镜像以 174 正在使用的镜像为基础，仅覆盖此次接入所需的 Python 文件，避免夹带工作区其他未发布修改。

## 本次实现

- 增加独立 CubeSandbox 配置和 Provider，复用 E2B 协议的文件、进程、工作区回收能力。
- API Bearer 鉴权与数据面分离；数据面使用每个实例独立的 `Host: 49983-<sandbox-id>.cube.app`，通过指定代理 IP 访问，无需修改机器 DNS 或 hosts。
- 默认由 Worker 运行 Claude CLI，首次隔离工具调用时分配 CubeSandbox，工具在远端执行，产物同步回平台；纯模型轮次可不创建沙箱。
- 需要完整远端 CLI 的任务走原有判定逻辑。Codex App Server 强制使用完整远端路径，避免其原生命令执行留在 Worker。
- 模板缺少 Claude/Codex CLI，且当前无法连通 Claude 官方安装站；完整远端执行时从 Linux Worker 离线上传对应二进制并验证版本。常规延迟工具模式不上传 CLI。
- 修复 E2B 进程等待被取消时的清理问题，保证超时仍终止远端进程，并向调用方返回正确的超时错误。
- 新建个人及空间草稿在默认执行配置被停用时，选择目录中已启用的配置。

### 174 数据迁移

迁移前无活动运行，`deployment_snapshots` 表为空。已备份并迁移 2 个租户能力目录、16 份已有草稿：

- 启用 `cubesandbox-private`，停用当前环境其他 Provider 对应的执行配置。
- 将已有本地执行草稿切到 CubeSandbox；维护草稿 revision，已发布草稿的修改按原服务规则递增草稿版本，保留已发布版本标记和内容。
- 原本明确允许本地执行配置的 MCP 绑定扩展至 CubeSandbox；未扩大其他 MCP 的允许范围。
- 新增 1 份独立集成测试草稿；最终 17 份草稿均使用 CubeSandbox。

## 验证证据

### 自动检查

- 相关 Sandbox、Claude/Codex transport、配置、生产组合、API 组合、Studio 目录、preflight 和草稿服务测试：**145 passed**。
- 相关代码 Ruff 检查通过；Pyright 指定项目 Python 环境后 **0 errors**。
- 新增回归覆盖每实例 Host 隔离、API 凭据不进入数据面、Bearer 兼容、缺失配置、延迟模式跳过 CLI、Codex transport、超时清理和新建草稿执行配置选择。

### 174 到 CubeSandbox 的真实协议测试

`scripts/smoke_cubesandbox.py --remote-cli --codex` 在候选镜像中运行：创建实例、中文文件上传、stdout/stderr、非零退出码、产物回收、命令超时后恢复、Claude SDK 双向协议初始化、Codex App Server 初始化、实例删除均通过；整组约 **10.14 秒**。

Codex 在本次验证中完成了协议初始化；174 当前启用的模型路由是 Anthropic-compatible，未将 Codex 的完整模型任务列为已验证。

### 正式应用两轮真实任务

通过与网页相同的 Studio HTTP API 创建草稿并交由正式 Worker 执行，使用 `deepseek-v4-pro`。

| 轮次 | Run ID | 结果 |
| --- | --- | --- |
| 首轮 | `run_c9ad250c51a24f9d819facb0790f13e1` | succeeded；Python 求 1–10 平方和得到 385，写入中文文件、读回并发布交付物 |
| 续跑 | `run_18f6391940774610bcc79f45a188f155` | succeeded；恢复同一会话文件，读取上一轮内容，计算 386，发布新文件 |

两轮工具事件均记录 `sandbox.provider=cubesandbox-deferred`。两个文件从正式下载接口取回并校验内容及 SHA-256：

| 文件 | 内容 | SHA-256 |
| --- | --- | --- |
| `cube-check.txt` | `385` + 换行 + `CubeSandbox 中文文件验证通过` + 换行 | `4cd5dc7666469f6746b269d84e81dca5c2dac39d509471fdefd13310f1612cf0` |
| `cube-continuation.txt` | `386` + 换行 | `3ad00987ec952f521f89cd901e4cf4fcf25100a3145613131a893a52bc78ee24` |

临时 canary 容器已删除。正式 API、3 个 Worker、quality-sync 均健康。

## 后续迭代建议

1. **固化 nexau-code 模板**：预装固定版本 Claude/Codex CLI 与常用运行依赖，省去完整远端模式的重复上传成本；模板变更后重新执行冒烟。
2. **资源和网络策略落实到基础设施**：Studio 的 CPU、内存、磁盘、TTL 与 network policy 字段是平台声明。本次未实现按 profile 动态配置 Cube VM 资源和细粒度出口策略，也未对基础设施强制执行这些声明作出保证。
3. **实例治理**：补充平台实例清单、指标、异常退出后的孤儿实例回收，以及模板/SDK 兼容性检查。
4. **多 Provider 路由**：当前仍为全局 Provider，后续按执行配置选择 Provider，允许 Cube/OpenSandbox/其他环境并存。存在已发布部署快照时，应保留快照绑定或显式生成新发布版本，不能直接篡改不可变快照。
5. **Codex 全链路**：配置支持的 OpenAI-compatible 模型路由后，再验证完整 Codex 模型、工具、审批与续跑任务。

## 回滚

174 发布目录保留 `compose.previous.private.json`、`compose.release.private.json`、`db-backup.private.json` 和 `migrate_174.py`。回滚须先停止接收新任务并等现有任务结束。

以下命令在 **174 主机**执行；此次仅准备了回滚流程，未实际回滚正式环境：

```bash
cd /data/cubesandbox-20260915-144135
docker compose -p agent-studio-174 -f compose.release.private.json stop api worker
docker compose -p agent-studio-174 -f compose.release.private.json run --rm --no-deps \
  -e CUBE_DB_BACKUP_PATH=/release/db-backup.private.json \
  -v /data/cubesandbox-20260915-144135:/release \
  --entrypoint python api /release/migrate_174.py rollback
docker compose -p agent-studio-174 -f compose.previous.private.json up -d --no-deps \
  --scale worker=3 api worker quality-sync
```

迁移脚本以事务执行，并检查 revision 与发布操作者。若已有草稿/目录在切换后被继续修改，脚本会拒绝覆盖，需先合并变更；**数据库回滚失败时不要继续切旧镜像**。切换后新增的 Cube 草稿保留内容，仅将执行配置回切本地。

详细选型背景见 [沙箱选型与演进分析](sandbox-selection-and-evolution-20260915.md)。
