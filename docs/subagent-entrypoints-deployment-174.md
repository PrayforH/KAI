# 子智能体入口调整：174 部署交接

用户明确要求：修改子智能体的创建、编辑和展示入口，并由本地 DSH Desktop 部署到 174。

## 本次行为

- AgentDraft / AgentDraftSummary 增加 parentDraftId，null 为独立入口，非 null 为内部子智能体。字段保存在现有 JSON payload 中，无需 SQL schema migration。
- 父智能体单页中显示“协作角色”，两字段新建并原子绑定内部子智能体；可以引用已有独立智能体，编辑配置并返回父智能体。
- 内部子智能体默认不进普通卡片列表、搜索或对话选择器；高级筛选可显示。可以将已经绑定的独立智能体移为内部子智能体，也可以恢复独立入口。
- 修改已发布内部子智能体后，返回父智能体会同步绑定到编辑后的版本；历史发布快照不改。
- 内部归属不依据名称或“被引用”推断；被引用的独立智能体仍然显示。
- 归属第一版面向个人智能体。协作空间保留引用已有智能体的入口，不支持在此新建专属子智能体。

## 必须部署的代码

仓库绝对路径：`/Users/xiaokai/Documents/agent studio`。实际前端是 `web/harness-console`，不是其他 web 子目录，也不是 agent-studio-model-management 工作树。

后端六个文件需一起更新，不能只更新 api.py：

- src/harness/studio/models.py
- src/harness/studio/service.py
- src/harness/studio/repositories.py
- src/harness/studio/api.py
- src/harness/storage/studio_repository.py
- src/harness/api/routes/agents.py

前端使用当前完整 web/harness-console 源码与 lockfile 构建。改动集中于 agent-studio-workbench.tsx、agent-studio.module.css、studio-client.ts、agent-studio.ts、task-agent-catalog.ts。

工作区有大量此前未提交改动。不要 reset、checkout、清理未跟踪文件或提交这些改动。不要修改业务代码来绕过测试。

## 部署方法及范围

本次授权升级 API、Worker、quality-sync 和 Web。旧 `docs/axis-web-174-build-deployment-runbook.md` 是仅前端部署手册，其“不更新 API”的范围不适用于本次；其 Web 构建、环境继承和回滚方法可复用。

目标 `172.20.109.174:3501`；不要操作 173。174 无外网，禁止临时打开公网或更改网络安全边界。

本轮临时 SSH 复用连接已建立，使用：

```sh
ssh -S /tmp/kai-subagent-deploy.kNQNRl/ssh root@172.20.109.174 '<command>'
scp -o ControlPath=/tmp/kai-subagent-deploy.kNQNRl/ssh '<archive>' root@172.20.109.174:/tmp/
```

不要输出或索取密码。若复用失效，报告该问题等待协调。

先只读检查当前容器、镜像、compose 标签和运行中任务。上一轮基线（部署前重新核对）：

- API/Worker/quality-sync: kai/axis-api:20260905-003452
- Web: axis-web-20260905-003452, kai/axis-web:20260905-003452
- Compose: /data/agent-studio/docker-compose，project agent-studio-174，env .env.production；包含 compose.yaml、compose.harbor.yaml 和当前 compose.axis-release-*.yaml（读取当前容器标签确定确切文件，不猜测）。
- Worker 数量 3；网络 agent-studio-174_default；Web 环境可从当前 Web 容器继承，勿输出环境值。

API 基础构建需要 cgr.dev，174 无法访问。基于当前运行 API 镜像制作增量镜像，将上述六个文件覆盖进已确认的安装目录。上一轮目录为 /app/project/lib/python3.12/site-packages/harness/。先检查再 COPY，保留原镜像用户/入口和环境。先在新镜像中做 import/compile 检查，再替换 API/Worker/quality-sync。三类服务同步部署以兼容 payload 字段。若有运行中任务，等待其结束再替换 Worker。

Web 在 174 用已有缓存依赖构建 amd64 镜像，临时 3599 冒烟，通过后切换 3501。不要复用旧 release ID。保留旧镜像和确切回滚命令；新 Web 保持自动重启策略。不要删除任何数据库、volume、用户任务或发布版本。

## 已有 helper-agent 归属迁移

用户要求该内部依赖不再占普通列表。升级后先核对以下现有记录及引用，完全匹配后使用新 placement API 修改该一条元数据：

- tenant local / user user_9a58184e4f8b4af1bcfa2f88b0dd2b7b
- parent draft_9a7bb4a061a749629fa166eb43a657d0，name public-opinion-agent
- child draft_4d2566edc69b441ea1d57c9be669a1e9，name helper-agent
- parent 的多个角色应引用 helper-agent@1.0.0。读取 child 当前 revision，PUT /v1/studio/drafts/{child}/placement，body 为 expectedRevision 和 parentDraftId。

通过 API 容器内已配置的服务认证调用，凭据只在进程内读取，禁止输出。该操作只更新草稿归属，禁止再次发布或更改 helper 现有发布版本。若 ID 不匹配或其他父智能体也引用 helper，停止该迁移并报告。

## 验证及交付

- 后端关键验证：uv run pytest tests/integration/api/test_internal_subagents.py tests/integration/api/test_agent_studio_api.py tests/unit/studio/test_service.py tests/unit/storage/test_studio_repository.py -q
- 前端 npm test -- --run，npm run build；git diff --check。
- 我方本地验证结果会随部署指令发送。收到“验证通过，开始部署”后可以直接使用，不需要重新安装依赖或重复全量本地测试。
- 174 检查容器健康和重启次数、首页/登录 HTTP、已认证 drafts/agents 接口。检查 helper parentDraftId 与普通 agents 排除该记录、父草稿角色仍完整。
- 检查 /openapi.json 有新的 subagents POST、placement PUT。
- 不要为健康探测创建真实模型任务，不要将历史事件回放称为真实新运行。
- 把 release ID、镜像/容器、接口验证、helper 迁移结果、回滚方式写入 /tmp/kai-subagent-deploy.kNQNRl/result.md，并在 DSH 会话报告。任何失败保留证据，不宣称部署完成。
