# 174：构建助手 Skill 共创、工具装配与联网修复

发布时间：2026-09-14。应用提交 `90e537c`；前序功能提交 `6ca1cc5`、知识库标识修正 `686940a`，均已推送 develop。

## 已部署行为

1. 构建对话通过现有 Skill 共创服务创建 / 更新完整 Agent Skill，包含正文、说明和附带文件。建议与工具 / MCP 绑定一起进入 DeepAgents 文件差异审阅，应用后按原草稿 revision CAS 一次性保存。创建不会覆盖同名 Skill，更新目标必须存在；不自动发布或运行生成的脚本。
2. 内置工具和 MCP 可从当前用户可见目录增减。服务端绑定生成时的目录修订，在生成、差异预览、应用三个阶段校验可见性、修订和编译约束。凭据继续由既有运行链解析；不会创建 MCP 服务或密钥。知识库 ID 与 MCP reference 是不同命名空间，知识引用仍通过已有知识库编辑入口装配。
3. 首页对话、智能体构建和效果测试的附件均在文件卡片内显示进度。使用浏览器上传字节计算真实百分比，传输完成后显示处理状态，服务端确认后才就绪；失败保留在卡片，需移除后重试。取消首页附件会中止传输；不再在输入框上方逐行列出上传状态。
4. 勾选公开联网工具的配置区显示当前账号实际联网状态及用户设置入口。

## 174 联网问题的证据与处理

- 174 平台联网开关为开启，默认搜索服务 MiniMax，密钥配置可用；实际使用账号的个人 `enabled` 保存为 false。173 同一账号为 true，因此其运行中出现 `harness-web` 且可以搜索。
- 按本次恢复联网要求，使用现有 `WebConfigurationService.configure` 将该账号 enabled 改为 true，保留其 provider 和加密密钥。运行仍读取用户持久化配置，不绕过开关，也未替换用户密钥。
- 网页读取另有兼容缺陷：Python 官网即使收到 `Accept-Encoding: identity` 仍返回 gzip，原读取器直接拒绝。现在支持 gzip / deflate 的有界解压，压缩字节及解压正文均保留 2 MB 上限，保留每跳公网 DNS 检查与 HTTPS 降级限制。
- 最终生产 lead-agent 验收运行 `run_f79e7965916143c7b9b398c5163dfd43` 成功：WebSearch 返回 5 个来源，WebFetch 返回 `Welcome to Python.org`，URL 为 `https://www.python.org/`。从持久化 tool.result 解析确认结果，并非仅采信助手自述。

## 验证

- 后端相关回归 105 项通过，最终计划解析与目录绑定调整后另复跑构建集成 4 项通过；Ruff 与相关 Pyright 检查通过。
- 前端 91 个测试文件通过，598 项通过、1 项跳过；Next.js 生产构建通过。覆盖上传字节事件、取消和错误、组件状态及构建附件流程。
- 最终候选镜像调用真实模型和 Skill 共创服务：创建 source-review、生成 assets/source-template.md、装配 WebSearch / WebFetch；确认预览不写草稿、应用后的所有项目文件与预览 after 完全相同、过期修订拒绝、其他用户不可读取。临时草稿已删除。
- 生产接口复验 1,215,000 字节文本完整预览、差异与保存文件一致性、权限隔离及修订冲突；临时草稿已删除。
- 生产 Web HTTP 200，16 个入口 chunk 和代码主题 chunk 均可加载，临时预览路由不存在。
- 浏览器自动化通道连续三次超时，本轮没有完成真实浏览器截图验收；不将构建与 DOM 测试等同于视觉验收。

## 镜像、配置与回滚

- API：`kai/axis-api:develop-20260914-90e537c`
  - ID：`sha256:cf7044e60d33f583992a78374be7b4199e124cccbe155b21bcffae61653b5c56`
- Web：`kai/axis-web:develop-20260914-90e537c`
  - ID：`sha256:62474bd4db7af89ab92b9b153806ec602a7b0b68f2f59f3eac2912778b822f8f`
- Web 容器：`axis-web-develop-20260914-builder-skills`，访问端口 3501。
- API、3 个 worker、quality-sync、Web 共 6 个服务 healthy，RestartCount 均为 0。
- 发布目录：`/data/kai-develop-20260914-builder-skills`，保留完整 Compose 覆盖链、原容器私有配置、数据库备份及回滚脚本。应用环境变量已比对保持一致，无新增数据库迁移。
- 原前端 `axis-web-develop-20260914-code-diff` 已停止并保留，原 API 镜像保留。可使用发布目录的 `rollback-api.sh` 和 `rollback-web.sh` 回滚应用；个人联网开关仍按本次恢复要求保持开启。
