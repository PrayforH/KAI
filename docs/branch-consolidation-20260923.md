# 分支整合与清理（2026-09-23）

## 长期分支与环境

| 分支 | 用途 | 对应环境 |
| --- | --- | --- |
| `develop` | 默认主分支，通用功能和已验证修复 | 174 |
| `auto/agent-evolution` | Agent Builder 开发、修复与演化验证；仍有待解决问题 | 173 |

GitHub 默认分支切换为 `develop`，CI push 触发覆盖这两条分支，Dependabot 目标改为 `develop`。
本次是源码分支整合，没有重新部署、重启服务或执行数据库迁移。环境说明遵循当前维护约定；
本文中的历史部署证据来自已有发布记录，不代表本次重新做过线上验收。

## 差异处理

- `main` 只有 16 个独有的历史 PR merge 提交：正常合并到 `develop`，树内容无变化，保留历史。
- `fix/sandbox-workspace-transfer`：把批量文件传输、一次归档回收及相关测试/记录合入 `develop`。
  该修复已有 174 发布记录，但之前只在本地分支上；见 `workspace-transfer-174-20260922.md`。
- `feature/automation`：把未合入的侧边栏账户区行布局修复合入 `develop`。
- `axis-web-release-20260919`：功能补丁已经进入主线，补回独有的最终发布记录 `0783815c`。
- `feature/deepagents-runtime`：配置修复已经有等价提交，把独有的 173 部署和 review 文档历史合入 evolution。
- `auto/codex-runtime`：二进制与部署 overlay 补丁已有等价提交；runtime 实现对应主线 `685264ce`，
  经 range-diff 核对为适配后合入，旧分支归档。
- `feature/web-console-enhancement`：早期另一套页面/路由原型，远落后当前控制台；保留归档，
  不把旧页面架构或旧数据库迁移覆盖到现在的主线。
- Dependabot 的 14 条旧分支及 PR：归档并关闭/删除；本次不混入未经验证的依赖升级。
  后续由 Dependabot 针对 `develop` 重新提出更新，可能产生新的临时分支。
- 其余分支已被两条长期分支包含，删除分支引用。
- 通用修复从 `develop` 同步到 evolution；未把尚未完成的 evolution 反向合并到 `develop`。

## 未提交工作与恢复

删除分支前创建了完整的 `all-refs.bundle` 并通过 `git bundle verify`。
本机备份目录为 `~/Documents/agent-studio-branch-backups/20260923-114115/`：

- `refs-before.txt`、`worktrees-before.txt`、`manifest.json`：原引用、工作目录、快照映射。
- 每个可用工作目录的 `staged.patch`、`unstaged.patch`、`working-files.tar.gz`（有改动时）：
  保存已暂存/未暂存差异和修改文件、未跟踪文件。原文件仍留在原工作目录。
- 清理旧分支时，引用它们的工作目录只切换为同一提交的 detached HEAD，不删除目录或修改文件。
  原主工作目录的未提交修改也按此方式原样保留，不自动覆盖到已整理的主线。
- 无法由两条长期分支直接追溯的旧分支，用 `archive/2026-09-23/` 下的 tag 保存提交，
  包括依赖升级和旧控制台原型；这是存档，不是活跃开发分支。

恢复单条旧分支（按下表使用完整 ref，避免与 tag 混淆）：

```bash
git bundle list-heads /path/to/all-refs.bundle
git fetch /path/to/all-refs.bundle refs/heads/OLD:refs/heads/recovered/OLD
# 原远程分支使用 refs/remotes/origin/OLD 作为来源
```

恢复未提交内容时，在对应原始提交的独立目录中先应用 staged.patch 并暂存，
再应用 unstaged.patch；未跟踪文件可从对应 working-files.tar.gz 按需取回。
不要直接把全部快照覆盖到更新后的 develop/evolution。

## 验证

- develop：相关后端测试 288 项通过；全量单元测试共 1465 项通过。
  首轮 1449 项通过，另外 16 项受本机 SOCKS 代理缺少 socksio 影响；去除测试进程代理变量后，16 项复跑全部通过。
- develop：侧边栏、任务项目、标题菜单、工作台以及消息历史相关前端测试 103 项通过（7 个测试文件）。
- evolution：合并后全量后端单元测试 1490 项通过；相关前端测试 136 项通过（11 个测试文件）。
- evolution 合并冲突仅涉及 `test_e2b.py` 的 import，两边测试及归档/独立超时能力均保留。
- 两条分支的改动 Python 文件 Ruff 检查通过；修正了合入测试的一处 import 排序问题。
- 本次不含重新构建镜像、生产环境端到端测试或完整 CI 数据库集成测试。

## 清理范围

21 条旧本地分支、18 条旧远程分支；关闭对应的 14 个旧依赖升级 PR。
17 个仍有独有提交的分支头由 `archive/2026-09-23/{local,remote}/...` 标签保存并推送。
两条长期分支之外的工作目录保留在原提交的 detached HEAD；失效的 worktree 注册记录清理掉。
原主目录仍保留未提交工作；后续开发应打开 `develop` 或 `auto/agent-evolution` 的工作目录，
不要在已归档的 detached HEAD 上继续提交而不建立新分支。

## 清理前引用清单

| 引用 | 提交 |
| --- | --- |
| `refs/heads/auto/agent-evolution` | `8a8ec5379939` |
| `refs/heads/auto/builder-test-conversations-174` | `dd9a2e90e642` |
| `refs/heads/auto/codex-conversation-controls` | `5a1a8751d38d` |
| `refs/heads/auto/codex-runtime` | `890634baad00` |
| `refs/heads/auto/cube-174-execution-check` | `8e811cf570d3` |
| `refs/heads/auto/cube-hitl-code-view-174` | `ec2e015dd286` |
| `refs/heads/auto/deepagents-develop-integration` | `083d9022a3f1` |
| `refs/heads/auto/deepagents-review-fixes` | `e7d4cdee2964` |
| `refs/heads/auto/fix-composer-controls-overflow` | `5bcd6655cfd4` |
| `refs/heads/auto/fix-deepagents-174-runtime` | `0c31b0571eba` |
| `refs/heads/auto/general-agent-conversation-contract` | `bf7bcd3453ff` |
| `refs/heads/auto/model-management` | `9dccbc8d7ff2` |
| `refs/heads/axis-web-release-20260919` | `0783815cf088` |
| `refs/heads/dev-final` | `ecf107d3bee4` |
| `refs/heads/dev-os` | `b756704b26b0` |
| `refs/heads/develop` | `21488020b2e3` |
| `refs/heads/evolution/composer-add-icon-20260922` | `8a8ec5379939` |
| `refs/heads/feature/automation` | `18e4d75d5582` |
| `refs/heads/feature/deepagents-runtime` | `448b7656e1e4` |
| `refs/heads/feature/weknora-knowledge-base` | `d30cb3aa233e` |
| `refs/heads/fix/develop-review-20260921` | `d69cb0d2cd1a` |
| `refs/heads/fix/sandbox-workspace-transfer` | `151e4a0225c6` |
| `refs/heads/merge/develop-builder-into-evolution` | `948e9b6fcc63` |
| `refs/remotes/origin/auto/agent-evolution` | `8a8ec5379939` |
| `refs/remotes/origin/dependabot/github_actions/actions/checkout-7.0.1` | `33d7df457c88` |
| `refs/remotes/origin/dependabot/github_actions/actions/setup-python-7.0.0` | `f221d2d6bdfd` |
| `refs/remotes/origin/dependabot/github_actions/astral-sh/setup-uv-10.0.1` | `7f4e2506ab9c` |
| `refs/remotes/origin/dependabot/github_actions/docker/login-action-4.6.0` | `3e0580b9d7cf` |
| `refs/remotes/origin/dependabot/github_actions/docker/setup-buildx-action-4.3.0` | `c5f1e3b25bc9` |
| `refs/remotes/origin/dependabot/npm_and_yarn/web/harness-console/assistant-ui/react-ag-ui-0.0.49` | `c1ec968519b2` |
| `refs/remotes/origin/dependabot/npm_and_yarn/web/harness-console/assistant-ui/react-markdown-0.14.8` | `95dcf8374c6f` |
| `refs/remotes/origin/dependabot/npm_and_yarn/web/harness-console/multi-9b1536b8cd` | `2b3b8a489daa` |
| `refs/remotes/origin/dependabot/npm_and_yarn/web/harness-console/types/node-26.1.2` | `0417817f18f9` |
| `refs/remotes/origin/dependabot/npm_and_yarn/web/harness-console/typescript-7.0.2` | `fb130eeeb63b` |
| `refs/remotes/origin/dependabot/pip/cryptography-gte-45-and-lt-51` | `9ad31db224ef` |
| `refs/remotes/origin/dependabot/pip/mcp-gte-1.28-and-lt-3` | `71af6976458a` |
| `refs/remotes/origin/dependabot/pip/pgvector-gte-0.4-and-lt-0.6` | `780a8f17ffab` |
| `refs/remotes/origin/dependabot/pip/redis-gte-6.2-and-lt-9` | `fbbc028121f1` |
| `refs/remotes/origin/develop` | `d69cb0d2cd1a` |
| `refs/remotes/origin/feature/automation` | `47f254a4a1eb` |
| `refs/remotes/origin/feature/web-console-enhancement` | `be0d44cb2681` |
| `refs/remotes/origin/feature/weknora-knowledge-base` | `d30cb3aa233e` |
| `refs/remotes/origin/main` | `bb3408eb291c` |
