# 174 CubeSandbox 模板切换

2026-09-16 按用户要求，将 174 的 CubeSandbox 模板由
`tpl-f116a5f3d1c442b2b1690f4d` 改为 `tpl-fb4a185de6184b69a94b1842`。

## 生效范围

- API 和 3 个 Worker 均已重新创建，容器健康检查通过。
- 实际配置键：`HARNESS_CUBESANDBOX_TEMPLATE`；API 的 `Settings()` 已确认读取新值。
- Compose 变更校验仅允许 API、Worker 的两个模板字段变化。
- 镜像仍为 `kai/axis-api:dsh-process-20260916`，镜像 ID 与切换前相同。
- 切换前活动任务查询结果为 `{}`。未修改数据库、Web、quality-sync 或模型配置。
- 新创建的沙箱使用新模板；未对已有沙箱进行原地替换。

## 验证

1. 切换前，在现有 Worker 中仅覆盖本次测试进程的模板变量，运行
   `scripts/smoke_cubesandbox.py --remote-cli --codex`，12.33 秒通过。
   验证中文文件上传、命令 stdout/stderr 与退出码、产物回收、命令超时后恢复、
   Claude SDK 协议初始化、Codex App Server 协议初始化。
   测试实例 `b432a0a47bdd4d9ca6a2dc1799613a08` 已删除。
2. 切换后，Worker 使用默认生效环境运行 `scripts/smoke_cubesandbox.py`，
   延迟工具执行模式测试 1.21 秒通过。
   测试实例 `e94c6376fd114b7aab84b0240c43eaed` 已删除。
3. API 和全部 Worker 健康；API `/healthz` 返回 200。

本次是模板兼容性与配置切换验证，未运行新的完整模型对话测试。

## 发布与回滚

174 主机发布目录：`/data/cube-template-20260916`。

- 当前部署配置：`compose.api.release.private.json`
- 切换前备份：`compose.api.rollback.private.json`
- 切换脚本：`switch.py`，包含活动任务检查、健康验证及失败自动回滚。
- 两份 Compose 文件均为 `0600`，含私有环境配置，不提交到代码库。

今后部署应以新的配置文件为基线，保留新模板值。需要回滚时，先确认无活动任务，
再在 174 主机执行（此次未执行回滚）：

```bash
docker compose -f /data/cube-template-20260916/compose.api.rollback.private.json \
  up -d --no-deps --force-recreate --wait --wait-timeout 90 --scale worker=3 api worker
```
