# 174 注册 115 OpenSandbox MCP 排查（2026-09-16）

目标：`http://172.20.109.115:8000/mcp`，内部网络，无认证。

## 已验证

| 检查位置 | 结果 |
| --- | --- |
| 174 API 容器直接 MCP initialize / tools/list | 145ms，19 个工具 |
| 174 Worker 容器直接 MCP initialize / tools/list | 138ms，19 个工具 |
| 174 Studio `/v1/studio/mcp/discover` | HTTP 200，检测耗时 167ms |
| Chrome 当前登录账号，新建 MCP 表单 | 连接成功，自动识别 Streamable HTTP，19 个工具，147ms |

服务名称为 OpenSandbox Sandbox，版本 1.30.0。端点支持 Streamable HTTP；旧 SSE 方式返回 400；普通 GET 缺少 MCP 所需 Accept 返回 406，这两者不能直接当作内网不通。

页面验证采用引用 `opensandbox-115`、服务名 `opensandbox_115`、内部网络、无需鉴权。只执行协议握手和工具列表查询，没有执行 sandbox_create、command_run、文件写入等业务工具，没有提交目录注册或授权执行环境。

## 尚未复现的环节

当前目录 revision 50，没有指向 115 的 MCP 记录。连接检测通过不能等同于保存成功。保存还要求合法引用/服务名、显示名称、能力说明、已检测工具；执行环境选择需启用且支持 internal 网络。页面默认网络范围为 external，需显式选择内部网络。

已向用户询问错误发生在“检测地址”还是“完成注册”，以及具体错误文本。尚无证据证明该地址存在连接、认证或 MCP 工具发现故障；未在缺少报错的情况下修改生产配置。
