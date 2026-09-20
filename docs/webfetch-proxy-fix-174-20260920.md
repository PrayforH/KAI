# WebFetch 代理参数修复与 174 验证

配置 `HARNESS_WEB_TOOLS_PROXY` 后，网页读取向 `httpcore.AsyncConnectionPool` 传入了字符串，但该参数要求 `httpcore.Proxy`。连接建立前会抛出 `AttributeError: 'str' object has no attribute 'url'`。搜索使用 HTTPX，因此不受这一错误影响。

修复将代理地址包装为 `httpcore.Proxy`，沿用既有出口、超时、网页内容限制与信任标记。新增四组回归覆盖 HTTP、HTTPS CONNECT，以及显式参数和环境变量两种配置来源。测试使用真实连接池与代理协议，仅替换底层网络 I/O；四组在修复前均复现错误。修复后联网工具及个人配置测试共 40 项通过，Ruff 和 diff 检查通过。

## 174 发布

发布目录：`/data/webfetch-proxy-20260920`。基于正在运行的镜像仅替换 `harness/runtime/web_tools.py`，已核对与线上原文件只差这一行。发布前对比 compose 和运行容器的配置，环境变量无变化；检查时没有活动任务。

| 服务 | 原镜像 | 修复镜像 |
| --- | --- | --- |
| API | `kai/axis-api:artifact-owner-20260920` | `kai/axis-api:webfetch-proxy-20260920` |
| Worker ×3 | `harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:deepagents-174-4f95e64b` | `kai/axis-worker:webfetch-proxy-20260920` |

四个容器均 healthy。保留原镜像与 `compose.before.yaml`，Web 和数据服务未更新。本地镜像未推送 Harbor。

## 验证

- 修复镜像通过 174 出口代理读取 `https://www.deepseek.com/` 成功，返回标题和 499 字符正文。
- 真实通用助手 `lead-agent@1.0.0` 验收：`run_eab778ad299f4eccb7e6f2b8bbb4f538`，状态 `succeeded`。`WebSearch`、`WebFetch` 的实际 `tool.result` 均无错误，网页标题为 `DeepSeek | 深度求索`。
- HTTP 代理协议由回归测试覆盖；真实 `http://example.com/` 探测被远端断开，并返回既有连接失败提示，没有再出现参数类型异常。不据此保证任意站点可达。
- 详细验收结果保存在发布目录 `verification.json`。测试通过服务授权创建独立会话，没有修改用户已有对话。

## 回滚

将 `compose.before.yaml` 恢复到 `/data/agent-studio/docker-compose/compose.deepagents-174.yaml`，使用现有 `.env.production` 和 compose.yaml、compose.harbor.yaml、compose.deepagents-174.yaml 组合，对项目 `agent-studio-174` 执行 `up -d --no-deps --scale worker=3 --timeout 120 --wait api worker`。先确认当前镜像仍是上表修复版本，避免覆盖后续发布。
