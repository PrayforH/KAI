# G18：CI/CD、供应链与最终生产就绪完成审计

日期：2026-07-16
分支：`feature/release-pipeline`

## 1. 结论

G18 已形成 build-once、promote-by-digest 的交付闭环。PR/main CI 覆盖 Python/Web、真实
PostgreSQL/Redis/MinIO、Agent 生产检查、Bundle 字节确定性、migration 单 head 与最新 revision
回退恢复、Fake Runtime smoke、Eval/Deployment Gate，以及 API/Web/Sandbox 镜像漏洞阻断。

Release 只构建一次三张镜像，输出 BuildKit provenance、三份 SPDX SBOM、三个可复现 Agent
Bundle 和规范化 release manifest；镜像、SBOM attestation 与 manifest 使用 GitHub OIDC
keyless Sigstore 签名。所有外部 GitHub Actions 均固定 40 位 commit SHA。

Promotion 只下载 `release-<source commit>`，不包含 build 步骤。test、canary、production 每次
都重新校验签名、release ID、Bundle/SBOM hash 与镜像签名。canary 必须证明 test、production
必须证明 canary 已运行相同 release ID、Agent version/package hash 和 Sandbox image digest。
任何 Gate 失败都会停止后续环境；若已完成切换，则恢复上一 Agent Snapshot 与应用镜像。

按用户决定，本 Goal 没有运营页面或运营 Tab。

## 2. CI 门禁

`.github/workflows/verify.yml` 是 PR、main 和 release 的同一可复用门禁：

- Ruff、Pyright、全量 Pytest；
- PostgreSQL 17、Redis 7、MinIO 真实依赖；
- Alembic 单 head、空库 upgrade、`downgrade -1 -> upgrade head`；
- Agent package check 和两次 pack 的 archive SHA-256 比较；
- Fake Runtime approval/resume/artifact/AG-UI smoke；
- Eval、Deployment、rollback、Session pinning、CAS 测试；
- Next.js tests/build；
- 三张生产 Dockerfile 构建；
- 已验证签名的 Trivy scanner，阻断有修复版本的 HIGH/CRITICAL 漏洞和高危 Secret/IaC 问题。

鉴于 2026 年 Trivy 供应链事件，未使用可变 `trivy-action` tag；扫描器镜像先校验 Aqua 的
Sigstore 身份。参考官方
[安全公告](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23)。

## 3. 不可变 Release 与同制品晋级

`harness.release/v2` manifest 包含：

- 平台 SemVer、hash-bound Release Notes、source commit 与 canonical `releaseId`；
- 每个 Agent 的 name/version、archive SHA、manifest hash、package hash；
- API/Web/Sandbox 的 registry reference、digest、SBOM path/hash。

`scripts/release_manifest.py verify` 会安全解包并重算 Bundle、清单与 SBOM。任何字节变化、
伪造 `releaseId`、可变 image tag 或 source commit 不符都会阻止晋级。

应用部署通过 `compose.release.yaml` 仅注入 digest image，并使用 `--no-build`。部署 Runner 用
文件锁和原子 manifest 更新维护 environment 的 current/previous/failed 状态。Agent 晋级先
上传同一 Bundle并比对服务端返回 hash，再通过 Eval/Quality Gate 与 environment revision CAS。
旧 Session 继续绑定原 Snapshot；canary 默认仅影响 10% 新 Session。

## 4. Rollback 与 migration 边界

后置健康 Gate 失败时：

1. 按逆序把有历史版本的 Agent 恢复到上一 verified Snapshot；
2. 从 Runner durable state 拉取上一组 image digests，以 `--no-build` 恢复 API/Web；
3. canary job 失败，production 不会自动触发。

数据库不做自动 destructive downgrade。migration 必须遵循 expand/contract，使 N-1 binary 在
回滚窗口内兼容 N schema。灾难恢复顺序、PostgreSQL/MinIO/Redis/Langfuse 权威边界和演练见
`docs/runbooks/rollback-disaster-recovery.md`。

## 5. 自定义角色决策

当前不交付自定义角色编辑器。保留审计过的 built-in member/admin/service identity 与明确
permission checks，避免引入未完成的角色委派、越权授予、双人审批和历史审计解释问题。只有
至少两个租户出现不同权限包的真实需求后，才按 immutable role revision 与 grant ceiling 设计
扩展。完整判定见 `docs/runbooks/final-production-readiness.md`。

## 6. 本地验收证据

```text
make verify
  => 633 passed, 4 skipped（跳过项均为显式 opt-in 外部 live 集成）

cd web/harness-console && npm test && npm run build
  => 35 files / 153 tests passed；15 routes production build passed

真实 PostgreSQL 17.5：
  alembic heads == 0015
  empty upgrade -> downgrade -1 -> upgrade head
  => passed

真实 PostgreSQL/Redis/MinIO 全量 suite
  => included in 633 passed

API / Web / Sandbox Dockerfile
  => all three local images built successfully

uv run pytest -q tests/unit/test_release_*.py tests/unit/test_final_readiness.py
  => release manifest/promotion/deploy/workflow/readiness tests passed

docker compose -f compose.yaml -f compose.release.yaml config --quiet
  => passed with digest-pinned image variables
```

GitHub OIDC 签名、GHCR push 和三个 protected environment 是外部部署动作，不能在未授权本地
任务中伪造为已执行。其 workflow、fail-closed 规则和本地可测核心均已交付；首次真实发布需
按 Runbook 配置 Runner/Environment 后保留 workflow URL、release ID 与 Snapshot ID。

## 7. 要求与证据

| 要求 | 证据 | 结论 |
| --- | --- | --- |
| PR/main 完整门禁 | reusable `verify.yml` + 全量本地运行 | 已证明 |
| Package/Eval/漏洞阻断 | CI steps、Gate client 与 failure tests | 已证明 |
| Bundle 签名/attestation/SBOM | `release.yml` + manifest/hash tests | 已证明（外部签名待首次 workflow run） |
| 同一 Hash 跨环境 | prior-environment exact release check | 已证明 |
| canary 失败停止并回滚 | workflow failure graph、snapshot/image rollback tests | 已证明 |
| 旧 Session 不漂移 | G10 lifecycle/API tests | 已证明 |
| migration 单 head/recovery | 真实 PostgreSQL 0015 验收 | 已证明 |
| 流水线不打印 Secret | token 仅 environment secret；错误不返回 body | 已证明 |
| 发布/回滚/DR Runbook | `docs/runbooks/*` | 已证明 |
| 自定义角色决策 | final readiness Runbook | 已证明 |
| G00～G19 审计齐全 | `scripts/final_readiness.py` | 已证明 |

## 8. 运行前外部检查

第一次真实 production promotion 仍必须完成人工 checklist，尤其是 GitHub production reviewer、
Runner mode-0600 env file、备份恢复的业务 RPO/RTO、真实 Daytona/gVisor、模型/MCP 网络和
Langfuse Trace。这些是目标环境事实，不能由仓库单元测试代替。
