# 两套独立环境定版与 174 恢复（2026-09-19）

## 1. 决策：174 与 173 是两套独立环境，各自迭代与验证

此前为验证"跨设备多 worker"曾把 174 整套环境停机（计划让 174 主机跑一个指向 173 队列的
远程 worker）。用户最终决策：**两套环境各自独立、分别做功能迭代和验证**，跨主机多 worker
方案不再推进（"中间件没关系"——为跨主机做的中间件开放不作为方案依赖）。

多 worker 的最终结论：

- 每套环境各自跑 **3 个本机 worker**（compose `--scale worker=3`），这本身就是多 worker
  形态，靠 Redis 队列 + Postgres fencing + Redis 会话门协作；
- 跨主机副本在代码层可行（分析见 `docs/develop-173-full-deploy-20260919.md` 第 6 节），
  但需要共享中间件可达性，本轮按决策不做；
- 173 侧已做的中间件开放**保留**：MinIO 已绑 `0.0.0.0:59000`（compose.yaml 有备份
  `compose.yaml.bak-lan`）；OTLP 重建因 Harbor 镜像 tag 被删而未完成，仍是
  `127.0.0.1:14317/14318`，如需开放先在本地 retag 运行中容器的镜像再重建。

## 2. 174 恢复过程与最终状态

停机期间发现并处理的两个问题：

1. **3501 被一个 9-15 的残留 web 容器占用**（`axis-web-composer-overflow-20260915`，带着
   compose 项目标签，被 `compose start` 一并拉起）。已 stop（未删除）；3501 让给新容器。
2. `seed` 一次性任务启动失败（HTTP 422，重复 seed 的既有行为），不影响服务。

最终状态（全部 healthy）：

| 容器 | 镜像 |
| --- | --- |
| api + worker ×3 | `kai/axis-api:develop-bfa9f8a`（env 沿用 `compose.api.release.report.json`：report + 模板校验） |
| web-1（3301，compose 内） | Harbor `composer-compact-20260813`（历史原样，未动） |
| **3501 控制台（用户实际访问）** | **`kai/axis-web:develop-680739c`**（容器 `axis-web-develop-680739c`，指向本环境 `http://api:8000`） |
| postgres / redis / minio / otel / quality-sync | 原镜像原配置 |

`/healthz` 200；3301/3501 均 200。

## 3. 新标签页按钮修复（680739c）

用户反馈 173 文件抽屉里看不到"新标签页打开"。排查结论：按钮已在 develop 且已部署，但原实现
只对**可预览类型**渲染（`kind !== "none"`）——`docx/xlsx/zip` 等落到 `none`，只有下载箭头。
修复：按钮对全部类型渲染（认证内容路由以 inline disposition 响应，浏览器能渲染就渲染、
不能就下载）。`680739c fix(console): offer the new-tab action for unpreviewable artifacts too`，
全量前端测试 708 passed。Web 镜像 `develop-680739c` 已部署到 **173:3301 与 174:3501** 两处。

## 4. 当前两套环境速览

| | 174 | 173 |
| --- | --- | --- |
| API/Worker | `develop-bfa9f8a`，worker×3 | `develop-bfa9f8a`，worker×3 |
| 控制台 | 3501 = `develop-680739c` | 3301 = `develop-680739c` |
| 沙箱后端 | CubeSandbox @111 | OpenSandbox @115 |
| 治理开关 | report + 模板校验 | report（无 cube 模板可校验） |
| 目录/数据 | 各自独立（postgres 各自本机） | 各自独立 |

## 5. 回滚

- 174 控制台退回旧版：`docker rm -f axis-web-develop-680739c` 后按
  `docs/axis-web-174-newtab-release-20260918.md` 第 5 节用 rollback 容器或旧镜像重启。
- 173 MinIO 收回局域网：`cd /data/agent-studio/docker-compose && cp compose.yaml.bak-lan compose.yaml`
  后 `up -d minio`。
