# Release and environment promotion runbook

## Purpose

This runbook promotes one immutable Harness release through `test → canary → production` without
rebuilding. A release consists of three digest-pinned images, reproducible Agent bundles, three SBOMs,
a canonical version-bound `release-manifest.json`, Sigstore signatures, SBOM attestations and BuildKit
provenance.

The workflow intentionally has no operations page dependency. GitHub protected environments are the
human authorization boundary; the Harness Deployment/Eval/Quality APIs are the runtime gate.

## One-time deployment runner setup

### Release build registry

By default the build-once job runs on `ubuntu-latest` and publishes to
`ghcr.io/<owner>/<repository>`. A private Harbor release instead configures repository values:

| Kind | Name | Example for 174 |
| --- | --- | --- |
| variable | `HARNESS_RELEASE_RUNNER` | Label of a trusted runner that can reach GitHub and Harbor |
| variable | `HARNESS_RELEASE_REGISTRY` | `harbor.shdata.com:5000` |
| variable | `HARNESS_RELEASE_NAMESPACE` | `agent-studio/amd64` |
| secret | `HARNESS_RELEASE_REGISTRY_USERNAME` | Least-privilege Harbor robot account |
| secret | `HARNESS_RELEASE_REGISTRY_PASSWORD` | Robot account token |

The release runner must reach the selected registry, the GitHub OIDC/Sigstore endpoints and the
Chainguard kubectl source. If Harbor uses an internal CA, install that CA on Docker/BuildKit and Cosign
before registering the runner. Do not add `--tls-verify=false` to the workflow or transmit a robot token
over plain HTTP. The current 174 host cannot reach GitHub/GHCR, so it cannot itself be this runner; use a
controlled bridge runner that can also reach the target deployment Docker endpoint, or provide approved
egress before enabling promotion.

Register a Linux runner with labels `self-hosted`, `linux`, `harness-deploy`. Create protected GitHub
environments named `test`, `canary`, and `production`; require reviewer approval for production.
Configure these environment values:

| Kind | Name | Meaning |
| --- | --- | --- |
| variable | `HARNESS_DEPLOY_ENV_FILE` | Absolute path to a pre-provisioned, mode-0600 Compose env file |
| variable | `HARNESS_RELEASE_STATE_ROOT` | Durable writable directory for `current/previous/failed` manifests |
| variable | `HARNESS_BASE_URL` | Control-plane URL reachable from the runner |
| variable | `HARNESS_WEB_URL` | Web URL reachable from the runner |
| variable | `HARNESS_TENANT_ID` | Release tenant |
| variable | `HARNESS_SMOKE_USER_ID` | Dedicated release identity used consistently for bundle publication, promotion, rollback and post-promotion chat acceptance |
| variable | `HARNESS_SMOKE_AGENT_NAME` | Runnable Agent included in every promoted release |
| variable | `HARNESS_SMOKE_AGENT_VERSION` | Exact version of the smoke Agent in the release manifest |
| variable | `HARNESS_EXECUTION_PROFILE` | Production-approved isolated profile, normally `isolated-default` |
| secret | `HARNESS_API_BEARER_TOKEN` | At least 32 random characters; never put it in workflow inputs |

The Compose env file contains target-specific secrets. It is never uploaded, echoed, copied into the
release artifact or passed to a Sandbox. The runner needs Docker/Compose, Docker Buildx, `jq`, write
access to the release state directory, read access to the env file and network access to GHCR, the
Harness API and Web.

Before creating a release tag, run `make release-infra`. The command performs only read-only GitHub API
requests and an anonymous `https://harbor.shdata.com:5000/v2/` TLS probe. It checks configuration and
secret **names**, never reads or prints secret values, and exits non-zero with all missing prerequisites.

## Build once

Before dispatch, update the same SemVer in `pyproject.toml`, `src/harness/__init__.py`, the Web
`package.json`, Helm `version`/`appVersion`, and add a dated `CHANGELOG.md` heading. Run
`.github/workflows/release.yml` manually for a signed candidate, or push the matching annotated
`v<SemVer>` tag for a promotable product release. A mismatch or `Unreleased` heading fails before any
image is built. The workflow first calls the complete reusable CI workflow, then:

1. builds API, Web and Sandbox images exactly once with BuildKit provenance and SBOM enabled;
2. blocks HIGH/CRITICAL fixed vulnerabilities;
3. packs every Agent twice and compares archive bytes;
4. writes SPDX JSON SBOMs and a canonical manifest binding platform version, source commit, every
   bundle and image hash;
5. keyless-signs each image, attaches each SBOM as an image attestation, signs the exact SBOM bytes
   and signs the manifest blob;
6. creates a canonically ordered, source-timestamped evidence archive, exact checksum and Sigstore
   bundle, then uploads
   `release-<commit>` as the only promotion input (the archive includes time-bearing Sigstore
   evidence, so its exact bytes are authenticated rather than incorrectly claimed reproducible);
7. for a tag build only, creates a **draft** GitHub Release after every build/sign step succeeds.

Record the platform version, release workflow run ID and source commit. Never promote a local directory
or a mutable tag.
The Trivy scan uses a signed scanner image rather than a mutable third-party Action because Aqua's 2026
incident explicitly recommends full-SHA Action pinning and signature verification:
[Aqua advisory](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23).

## Promote in order

Run `.github/workflows/promote.yml` with the release run ID, source commit and `test`. Repeat with
`canary`, then `production` only after the previous job and its observation window are accepted.

At every environment the workflow:

1. checks out the exact source commit, logs into the selected release registry using the
   least-privilege robot secret, downloads `release-<commit>` and requires
   `harness.release/v2`; legacy v1 manifests remain readable only for local rollback state;
2. verifies the manifest Sigstore identity, canonical release ID, bundle/SBOM hashes, exact SBOM blob
   signatures, image-bound SPDX attestations, image signatures and BuildKit SLSA provenance; each
   provenance VCS revision must equal the requested source commit;
3. pulls exact `reference@sha256:digest` images, runs the forward migration, starts services with
   `--no-build`, waits for health and runs the idempotent seed;
4. uploads the exact Agent bundles and verifies the server-returned manifest/package hashes;
5. evaluates offline Eval and online Quality gates;
6. checks that canary was already proven in test, and production in canary, with the same release ID,
   Agent version/package hash and Sandbox image digest;
7. promotes using environment revision CAS and waits for a durable `succeeded` terminal state;
8. verifies the configured smoke Agent name/version is part of the signed release manifest, then runs
   one authenticated AG-UI streaming question without publishing or mutating an Agent package; the run
   must emit text containing a per-run marker and terminate with `RUN_FINISHED`;
9. checks the API and Web endpoints and uploads latency, health, attestation and rollback evidence even
   when a post-deployment gate fails.

Before **any** production deployment or migration, the promotion job additionally proves that the
matching `v<platformVersion>` tag resolves to the requested source commit and that the signed Release
already exists as a draft. A manually dispatched, untagged candidate can be tested and canaried, but
cannot mutate production and fail only afterward during publication.

Each promotion run derives an `operationId` from the GitHub run ID and attempt. A workflow retry can
therefore create a fresh promotion after an earlier deployment failed, while all requests within one
attempt remain idempotent. The recovery plan is checkpointed after every Agent request and keeps its
original previous-snapshot targets immutable, so retrying rollback cannot accidentally reverse back to
the failed snapshot.

After a successful protected production promotion, a separate GitHub-hosted job proves the
`v<platformVersion>` tag points to the promoted source commit and publishes the existing draft Release.
The workflow never creates a public Release before signed artifacts exist, and never publishes the
draft before production acceptance. A manually dispatched candidate intentionally cannot complete this
publication step; run the matching immutable tag workflow for the production release.

`canary` defaults to 10% and only affects new Sessions. Existing Sessions remain pinned to their
original Deployment Snapshot. Concurrent promotion to the same environment is serialized by GitHub and
still protected by Harness environment revision CAS.

## Acceptance evidence

Retain the workflow URL, source commit, `releaseId`, image digests, bundle hashes, environment snapshot
IDs, Eval/Quality gate response and final health result. Do not retain the service token or env file.
The promotion workflow retains `work/` for 90 days, including the AG-UI timing sample and any generated
promotion/rollback plan. The release artifact retention is also 90 days; production evidence that must outlive it should be copied to
the organization's approved immutable audit store.
