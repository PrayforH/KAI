# 174 Web 发布：文件预览"在新标签页打开"按钮（2026-09-18）

本次回答并修复：`feat(console): open a previewed file in its own tab` 是否已合并、为什么在 174 上看不到。

## 1. 合并状态

- 提交：`0e63eff feat(console): open a previewed file in its own tab`（2026-09-18 18:26:21 +0800），作者 xiaokai。
- 位于 `origin/develop`，并已随本次合并 `11a22c3` 进入 `feature/weknora-knowledge-base`（`git merge-base --is-ancestor 0e63eff HEAD` 通过）。
- 改动 4 个文件：`web/harness-console/src/components/rail-file-preview.tsx`（新增外链按钮 + `ExternalLinkIcon`）、`src/app/conversation-experience.css`（`.rail-preview-actions` / `.rail-preview-action` 样式，已在基线上）、`src/app/api/harness/artifacts/[artifactId]/route.ts`、`tests/rail-file-preview.spec.tsx`。

## 2. 为什么 174 上"看不到"

代码已合并，但 **174 控制台跑的前端镜像里没有这份代码**。切换前的实际状态：

| 项 | 值 |
| --- | --- |
| 3501 上的容器 | `axis-web-summary-row-20260918` |
| 镜像 | `kai/axis-web:summary-row-20260918`，构建时间 **2026-09-18 16:23:38**（比 `0e63eff` 早 2 小时） |
| compose 的 `agent-studio-174-web-1` | `…agent-studio-web:composer-compact-20260813-c68d8c5`（8 月 13 日） |

镜像内资源比对（ASCII 标记，避免压缩器转义中文导致的假阴性）：

| 标记 | summary-row（旧） | 本次新镜像 |
| --- | --- | --- |
| `rail-preview-download`（旧预览按钮） | 3 个文件 | 有 |
| `rail-preview-action`（新标签页按钮） | **0** | 3 个文件 |
| `M6.4 3.6H3.6v8.8h8.8V9.6`（外链图标路径） | **0** | 2 个文件 |

即：本次 API/Worker 发布（见 `docs/merge-develop-174-release-20260918.md`）只切换了 `api` + `worker`，没有构建/切换 Web，所以前端保持旧构建。

## 3. 本次 Web 发布

Release ID：`20260918-185931`（按 `docs/axis-web-174-build-deployment-runbook.md` 执行）

- 本地门禁：`npm test -- --run` → 109 文件 / **708 passed, 1 skipped**；`npm run build` 成功；`git diff --check` 无输出。
- 打包上传（排除 `node_modules`/`.next`）→ 174 `/data/axis-web-20260918-185931` 远端构建。
- 新镜像：`kai/axis-web:20260918-185931`（`sha256:c19f2390b208…`，created 19:00:05）。
- 部署前只读检查：3501 唯一占用者为 `axis-*` 容器；`api=running/healthy`；3599 空闲。
- 3599 冒烟：`/` 200、`/icon.svg` 200、`/api/auth/config` 200、`/api/harness/runtime-config` 200、`/api/auth/session` 401（未登录预期）、`/studio/spaces` 404（已移除页面预期）；容器 `running/healthy`、restarts 0、日志无 Next.js 错误。
- 可回滚切换：旧容器改名为 `axis-web-summary-row-20260918-rollback-20260918-185931`（保留，未删除），新容器 `axis-web-20260918-185931` 绑定 3501 并带 `--network-alias web`。

## 4. 上线后验收（针对本次功能）

从 3501 实际抓取服务端产出的 bunde 校验：

```
HTTP 200 | http://127.0.0.1:3501/_next/static/chunks/2ag0b6ckk-g1y.js
   rail-preview-action=1  external-link-icon=1  aria-label(在新标签页打开)=1
HTTP 200 | http://127.0.0.1:3501/_next/static/chunks/3guxufoeri_v3.css
   rail-preview-action=1
```

前端 bundle 是哈希文件名，浏览器会继续用旧缓存；**验收时需要硬刷新（Cmd/Ctrl+Shift+R）**。

## 5. 回滚

```bash
# 停掉新容器，把 rollback 容器改回原名并启动
docker rm -f axis-web-20260918-185931
docker rename axis-web-summary-row-20260918-rollback-20260918-185931 axis-web-summary-row-20260918
docker start axis-web-summary-row-20260918
```

## 6. 同批次的目录变更（非 Web）

用户反馈"MCP 列表里没有 `sentiment_query_mcp`"。排查结论：目录接口 `/v1/studio/capabilities` 按用户过滤（`catalog_service._record_for_user`：平台 MCP（`ownerUserId` 为空）+ 本人名下 MCP）。该 MCP 的 `ownerUserId` 是 `user_44229d56651b44a8b91f47dc446dc275`（admin@shdata.com），因此只有 admin 登录时可见，其余账号（含 xiaokai@shdata.com）列表为空。该过滤逻辑自 2026-08-03 的 `2e2c217` 起就存在，与本次合并无关。

按用户要求为另一账号也配置一条（保留原有条目，追加同 reference 的不同 owner；schema 以 `(owner_user_id, reference)` 去重，允许并存）：

- 变更：`capability_catalogs`（tenant `local`）revision **75 → 76**，`updatedBy=system:merge-develop-20260918`，`mcpServers` 由 1 条变为 2 条，reference 均为 `sentiment_query_mcp`。
- 可见性验证：`admin` → `[sentiment_query_mcp]`；`xiaokai` → `[sentiment_query_mcp]`；其它账号（如 liyw）→ `[]`。
- 若需要所有账号可见，把两条的 `ownerUserId` 置空即可（改为平台级 MCP）。
