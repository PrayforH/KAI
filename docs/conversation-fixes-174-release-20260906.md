# 对话体验修正发布 · 20260906-014427

目标：`http://172.20.109.174:3501`。
任务清单：[6 项反馈及 2 项补充](plans/2026-09-06-conversation-fixes.md)。

## 变更与验证

- 恢复组件原有中文组合输入同步；测试覆盖拼音候选、中文提交、IME Enter、Safari compositionend 后的 Enter。
- 回答操作栏在点赞/点踩后仅显示 HH:mm；运行耗时保留在顶部。
- 内部子智能体开关移到个人设置「配置」，设置页改为左右布局及 800px 内容列。
- 去掉输入框底部提示与发送后的成功提示占位，缩小发送/停止按钮。
- 回复区回撤新增的进展/模型摘要标签及重复占位；原始可观察文本与过程折叠保留。
- 按任务查询文件和校验下载；任务切换时立即隔离旧状态；同名文件、跨任务访问、切换请求延迟均有回归覆盖。
- 技能创建跳转与 `$` 选择共用技能标记；引导横栏采用「补充内容 / 调整方向 / 删除 / 更多」，保留编辑、排序和多任务排队。

前端：73 个文件、484 项测试通过，生产构建及 TypeScript 通过。
后端：文件与 AG-UI 回归 72 项通过；Ruff、Pyright 通过。浏览器用隔离模拟 Runtime 验证关键交互，不发送收费模型任务。

## 部署

- 远端源码：`/data/axis-fix-20260906-014427`。
- Web 镜像 / 容器：`kai/axis-web:20260906-014427` / `axis-web-20260906-014427`。
- API、3 个 Worker、quality-sync 镜像：`kai/axis-api:20260906-014427`。
- API 基于前一版 .152 SDK 镜像更新源码，无数据库迁移。
- npm 镜像站与官方源在 174 下载受阻，使用 `web-update.Dockerfile` 与现存依赖构建镜像完成远端编译。核对过既有依赖的 version/integrity 完全一致；仅新增用于本地 DOM 测试的 jsdom，不被生产构建引用。
- 部署前检查无活跃 Run；Compose 的服务环境变量与原容器一致。
- 原有 3301 Web、数据库、Redis、MinIO 和智能体版本保持原状。

## 回滚

旧 Web 容器 `axis-web-20260906-004236-recovery` 与旧 API 镜像保留。

```bash
docker stop axis-web-20260906-014427
docker start axis-web-20260906-004236-recovery
cd /data/agent-studio/docker-compose
docker compose --profile observability --env-file .env.production \
  -p agent-studio-174 -f compose.yaml -f compose.harbor.yaml \
  -f compose.axis-release-20260906-004236.yaml \
  up -d --no-deps --no-build --scale worker=3 api worker quality-sync
```

回滚前核对 3501 的实际占用，回滚后检查首页、`/healthz` 和容器健康。

## 最终验收

已完成正式切换。Web、API、3 个 Worker、quality-sync 均 healthy，restart 0。首页、图标、鉴权配置、runtime config 和 API healthz 均返回 200；未登录 session 返回 401。临时冒烟容器与本地模拟运行服务已清理。

Web 镜像 ID：`sha256:f3239e13c84f9b1de54c4bdd7a5c565ee7a3ad8986bac748630f174a6948d3e3`。
