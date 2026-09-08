# Agent Studio 0.2.0 release-candidate audit

Date: 2026-08-09
Source baseline: `797d73316178704107178bdbc70e8726ad6818fa` plus the documented dirty P0/P1
worktree
Formal status: **not released**

The existing public `v0.1.0` tag points to `26b672f` and its Release workflow
`31122494040` failed in container-security before build-once/signing. It does not contain the current
workspace/context/security implementation and must not be moved or reused; the next candidate is 0.2.0.

## Proven in the current candidate

| Requirement | Evidence | Status |
| --- | --- | --- |
| Python/Web/Helm/Changelog use one SemVer | `scripts/check_release_version.py --expected 0.2.0` | PASS |
| Manifest binds version, source, bundles, images and SBOMs | `harness.release/v2` tests; v1 remains rollback-readable | PASS |
| Public release state | Signing success creates draft; production success publishes the same tag/commit | PASS in workflow tests; live run missing |
| Backend release gate | Ruff, Pyright 0, Agent determinism, migration `0026`, 1031 passed / 4 skipped | PASS after promotion identity/pointer update |
| Frontend gate | 306/306 tests and Next.js 16.2.11 production build, 19 static pages generated | PASS after 0.2 metadata update |
| Remote release-preparation fixes | CI test DB/quota and knowledge owner fix absorbed; Pyright 284-error waiver rejected | PASS |
| Candidate repository hygiene | Secret/private-key/legacy-registry scan clean; untracked evidence is 4.7 MB, dominated by reviewable Trivy JSON | PASS; selective staging still required |
| Executable infrastructure audit | `scripts/check_release_infrastructure.py --repo PrayforH/agent-studio`; `release-infrastructure-20260809.json` | FAIL as designed: all external prerequisites reported |
| Exact deployed image vulnerability scan | gray2 API/Web 0 HIGH / 0 CRITICAL; Sandbox candidate 0/0 | PASS for dirty gray candidate |
| 174 runtime | API/Web/3 Worker/quality-sync and dependencies healthy; migration head 0026 | PASS |
| Real chat/cancel | loopback and external chat 5/5; cancellation 30/30 | PASS |
| Workspace isolation / `fb472b2` | owner 200, outsider 404 and hidden from outsider list | PASS |
| Signed clean 0.2.0 release and ordered promotion | No clean commit, signed workflow run or promotion run exists | MISSING |
| GitHub protected environments | Repository currently reports no environments | MISSING |
| Deployment runner | Repository currently reports zero self-hosted runners | MISSING |
| Runner network path | 174 cannot reach GitHub or GHCR; bridge runner/approved egress absent | MISSING |
| Harbor release endpoint | Required endpoint is `https://harbor.shdata.com:5000/v2/`; anonymous verified-TLS probe is not currently reachable | MISSING |
| Transactional deployment rollback | Fault injection retains the failed candidate, preserves current until success, restores prior digest without migrate/seed; env secrets require owner-only permissions | PASS in deterministic tests; live canary fault still missing |
| Retry-safe Agent promotion | GitHub run/attempt scopes promotion idempotency, every partial plan is checkpointed, and repeated rollback retains the original recovery target | PASS in deterministic tests |
| Promotion identity and convergence | Publication, environment mutation, rollback and black-box chat share one release user; both promotion and rollback re-read the durable healthy snapshot pointer | PASS in deterministic tests |
| Production pre-mutation boundary | Exact `v<version>` tag/commit and draft Release are verified before production deploy or migration, then verified again before publication | PASS in workflow tests; live run missing |
| Private registry promotion auth | Promote workflow performs an explicit least-privilege Harbor login before Cosign, provenance inspection and digest pulls | PASS in workflow tests; live TLS/login missing |
| Desktop/mobile browser acceptance | `/studio/agents` loaded with title `智能体 · Agent Studio`; full DOM/mobile visual run timed out | INCOMPLETE |

## Release decision

The code and 174 gray deployment are release-candidate shaped, but formal production approval would be
incorrect today. The remaining sequence is:

1. review and commit the complete P0/P1 worktree as one auditable 0.2.0 release candidate;
2. provide a trusted runner/network path and configure `test`, `canary`, `production` environments;
3. configure the signed release registry (Harbor for 174) with TLS/internal CA and a robot account;
4. run the clean `v0.2.0` signed Release and retain its workflow URL, manifest and attestations;
5. promote the same digest through test → canary → production, including the real AG-UI marker gate;
6. complete desktop/mobile visual acceptance and deliberately failing canary rollback evidence.

This document is an audit of evidence, not a waiver for missing evidence.

The infrastructure preflight is intentionally read-only: it queries GitHub environment, variable,
secret-name and runner metadata plus the anonymous Harbor v2 endpoint. It never requests or prints
secret values. Re-run `make release-infra` until it returns `"ready":true` before creating `v0.2.0`.
