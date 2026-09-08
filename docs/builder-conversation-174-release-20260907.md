# 多轮修改智能体 · 20260907-172252

正式环境：http://172.20.109.174:3501

## 交互

- 构建助手增加「试跑 / 修改配置」。普通测试消息不自动修改配置。
- 创建完成后继续同一草稿会话；关闭/重开侧栏保留当前会话。明确点击新建才开始新的创建会话。
- 修改模式基于服务端最新草稿与最近最多 20 条修改消息，提供修改前/后的预览。
- 支持显示名称、简介、系统提示词、任务输出要求、已有 Skill 正文及协作角色职责；可缩减 Tools/MCP/知识库或移除 Skill。
- 不允许对话扩大权限、增加工具、改变模型/运行环境/标识/归属、改写脚本或自动发布。这些操作仍使用主编辑区的明确入口。
- 「应用修改」通过 expectedRevision 并发检查写回同一草稿；「应用并重新试跑」使用原测试任务和新修订，不把修改指令当测试任务。
- 试跑期间可生成/应用修改，但当前运行仍使用其原配置快照；并行再次试跑被禁用，结束后可重试。
- 生成期间的服务端版本变化、应用时的旧修订和主区未保存内容均有冲突保护。切换草稿后丢弃旧的异步建议返回。
- 已发布版本不被改写；沿用既有草稿保存的自动版本递增逻辑。应用修改不等于发布。
- 已保存配置持久化到既有草稿存储。对话和未应用建议属于当前页面会话，刷新页面不会恢复聊天记录；本版不新增构建会话存储表。

## 实现

后端新增 `src/harness/studio/builder_conversation.py`；修改 `studio/service.py` 与 `studio/api.py`：

- POST `/v1/studio/drafts/{draft_id}/builder-conversation`：鉴权、读取最新草稿、调用已配置模型渠道、结构/边界/新增配置错误校验，只返回建议，不写草稿。
- POST `/v1/studio/drafts/{draft_id}/builder-apply`：重新鉴权与校验建议，CAS 保存；没有 SQL schema migration。
- 复用 ModelConfigurationService.complete_text（支持 Anthropic/OpenAI 兼容渠道），并不模拟模型回复或从错误文本直接修改配置。
- Skill 修改仅替换正文，保留 files/source 等原字段；子角色仅改职责，保留引用与并行设置。

前端沿用现有侧栏与主题 token，补齐用途切换、修改预览、确认/放弃、应用结果和冲突状态。变更在 `agent-builder-overlays.tsx/.module.css`、`agent-studio-workbench.tsx`、`studio-client.ts`。

## 验证

- 前端全量 78 文件 / 505 项通过，最终本地生产构建通过。
- 后端相关 69 项通过；Ruff、Pyright 与 git diff --check 通过。
- 新增后端接口测试覆盖多轮基于同一草稿、生成不落盘、应用修订递增、跨用户隔离、非法权限扩展、无效模型 JSON、澄清、生成期间并发编辑和旧修订拒绝。
- 新增 React 交互测试覆盖确认前不保存、旧测试任务重新试跑、主区脏编辑保护、运行期间修改、创建后持续修改与显式新建重置。
- 174 实际使用 deepseek-v4-flash 渠道做两轮「仅修改简介」预览。两轮均只返回 description 变更；对比请求前后完整草稿相等（修订 4，版本 0.3.23）。未调用 apply，也未启动真实业务试跑。
- 3599 临时 Web：首页、认证配置、运行配置、智能体页面均 HTTP 200。接口 OpenAPI 包含两个新入口。
- 正式切换后 API、三个 Worker、quality-sync、Web 共六个服务均 healthy、restart 0；3501 智能体页/登录页 HTTP 200，8800 healthz 为 ok。镜像内三个后端文件 SHA256 与最终本地源码一致；临时冒烟容器已移除。
- 未进行已登录浏览器的人工视觉验收；接口与 React 交互测试不是该验收的替代声明。

## 部署

- 源码构建目录 `/data/kai-builder-20260907-172252`。
- API 基线 `kai/axis-api:20260907-115304`。逐文件比对基线，只覆盖上述三个后端文件；ModelConfigurationService 与基线完全相同。无依赖升级。
- API 镜像 `kai/axis-api:20260907-172252`，ID `sha256:485f45c5417e1a061de6e162ea8afe469342ec259de88a69ce1b710236a4d720`。
- Web 镜像 `kai/axis-web:20260907-172252`，ID `sha256:d1be69a0ca5f9c906f72a7b71058f32d65ae77db095914278f37b3a0a2c32569`，使用 174 已有构建依赖，lockfile 与上一版 SHA256 相同。
- Web 容器 `axis-web-20260907-172252`，3501，继承旧容器环境，restart unless-stopped。
- API / 3 个 Worker / quality-sync 在既有 Compose 文件之上追加 `compose.axis-builder-20260907-172252.yaml`，未运行 migrate/seed。切换前活动任务计数为 0。
- 保留旧 Web `axis-web-20260907-115304` 及旧镜像。未操作 173、旧 3301 Web、数据库、volume 或任何业务发布版本。

## 回滚

确认无活动任务后：

```sh
docker stop axis-web-20260907-172252
docker start axis-web-20260907-115304
cd /data/agent-studio/docker-compose
docker compose --profile observability --env-file .env.production \
  -p agent-studio-174 -f compose.yaml -f compose.harbor.yaml \
  -f compose.axis-release-20260906-132229.yaml \
  -f compose.axis-unmetered-20260907-115304.yaml \
  up -d --no-deps --no-build --wait --scale worker=3 api worker quality-sync
```

该回滚保留此前取消运营额度限制的版本，不会退回旧预算门禁。已保存的草稿修改不会因回滚程序而撤销。
