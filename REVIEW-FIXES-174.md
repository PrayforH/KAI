# reviewfix-20260921：develop 代码审查结论的修复与 174 验证

修复分支：`fix/develop-review-20260921`，基于 `develop@7c76d761`。
提交：`aa0e8f35`（修复）、`118298f6`（174 验证脚本）、`fe9bb761`（运行态验证脚本）。
审查文档：`docs/develop-code-review-20260921.md`（本目录的部署验证对应的就是那份 review）。

## 部署内容

| 组件 | 镜像 | 说明 |
| --- | --- | --- |
| API | `kai/axis-api:reviewfix-20260921` | 基于原运行镜像的增量层，只覆盖 `src/harness` |
| Worker ×3 | `kai/axis-worker:reviewfix-20260921` | 同上 |
| Web（3301） | `harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:reviewfix-20260921` | 本地 buildx 构建 linux/amd64 后推送 |
| Web（3501） | 同上 | `update_3501.sh reviewfix-20260921`，旧容器保留为 `axis-web-submenus-20260921-rollback-reviewfix-20260921` |

无数据库迁移：本次只改 `src/harness` 与 `web/harness-console`。

镜像内 `harness` 包与本地源码逐字节一致：318 个 Python 文件，聚合 SHA-256
`48760584b42e676072b8ea74402c5337b3f6e2d370d007e5da2898bb2df235de`。

## 验证结果

| 检查 | 方法 | 结果 |
| --- | --- | --- |
| 部署健康 | 4 个后端容器 healthy、`/healthz`、3301/3501 均 HTTP 200 | 通过 |
| 沙箱就绪预算 | 容器内 `Settings().opensandbox_ready_timeout_seconds` | `180`（修复前被重复字段覆盖为 90） |
| 模板校验默认值 | `Settings().cubesandbox_validate_template`，且 API 以该默认值启动成功 | `True` |
| 文档归属（P0） | `scripts/verify_174_document_ownership.py`：源 `aipolicy` 读 `overseas` 的文档 | 详情与切片均 `refused`；同一文档经自己的源仍可读；引擎侧同一请求仍是 200 |
| 内部资产脱敏 | `scripts/verify_174_redaction_and_prompt.py`：`python -c open(...)`、`sort`、`cat` | 全部识别为需脱敏；用户产物与普通命令不误伤 |
| Skill Creator 提示词 | 同上：五个必需小节 + 产物名与校验一致 | 5/5 小节，`demo-skill.skill` 与 `evals/evals.json` 均在提示词中 |
| 真实运行回归 | `scripts/verify_174_real_run.py`：真实入队 → worker → 沙箱 → 模型 | `run_538d410de5094c5e83db30a555c23368` 终态 `succeeded`，runtime=deepagents，`is_error=false`，1 轮，5013/348 tokens，完整回答 |
| 前端产物 | 两个 web 容器内 `.next/static/chunks` 检索 | 新文案（本机隐藏、运行环境未能启动、工作日每/每天每）命中；旧的裸错误码兜底文案为 0 命中 |
| 本地回归 | `pytest tests/unit`、`vitest run` | 见仓库测试结果；新增测试覆盖跨库归属、租约 CAS、按路径脱敏、标题退避、无 vendored 树、排程往返 |

## 未在本轮验证的项（诚实记录）

- **租约 CAS 的线上并发**：断言的是"判定在 UPDATE 内、且不先读"，由本地真 Postgres 集成测试
  （`tests/integration/storage/test_sandbox_lease_postgres.py`）覆盖；未在生产库上制造并发写。
- **工具门覆盖规则**：回归测试在本地证明旧行为会失败，上线的镜像内含同一份代码；未在 174 上构造显式 DENY 策略。
- **自动化排程的界面级操作**：cron 往返由单元测试覆盖，部署产物检索确认代码在内，但没有在浏览器里点开一个既有任务再保存。
- **标题生成退避**：属于进程内状态，只由单元测试覆盖。

## 回滚

- 后端：把 `compose.deepagents-174.yaml` 的 `api`/`worker` 镜像改回 `kai/axis-api:cube-v1-20260921` /
  `kai/axis-worker:cube-v1-20260921`，执行 `up-deepagents-174.sh`。备份在
  `/data/agent-studio/backups/reviewfix-20260921/`。
- 3301：同样回滚 `web` 的镜像行并重建该服务（同一备份目录含改前文件）。
- 3501：`bash /data/agent-studio/update_3501.sh submenus-20260921`，或直接
  `docker rename/start` 已保留的 `axis-web-submenus-20260921-rollback-reviewfix-20260921`。

## 已知与本轮无关的既有问题

- 174 主机上 `npm ci` 在 Docker 构建中崩溃（`Exit handler never called!`），所以 web 镜像改为
  在本地构建 amd64 并推 Harbor，再在 174 拉取。
- `docs/deployment.md` 等文档里的历史设计记录仍提到 `tavily-readonly`，属历史文档；
  运行手册（`local-development.md` / `deployment.md` 的配置段）已按当前实现改写。
