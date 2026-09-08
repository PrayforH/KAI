# Rollback and disaster recovery runbook

## Automatic rollback boundary

If a post-promotion health gate fails, the CD workflow first rolls each Agent environment back to its
previous verified Deployment Snapshot, then restores the previous API/Web image manifest from the
deployment runner's durable release state. A failed canary stops the workflow; production is never
started by that run.

Rollback never rebuilds, retags or edits an Agent version. It uses the previous signed snapshot and
`reference@sha256:digest`. If an environment has no prior snapshot, the release is stopped and isolated
there; there is nothing valid to restore, so the incident requires manual containment rather than an
invented rollback target.

## Personal Agent version rollback

For a personal Agent, open Studio, choose **版本历史**, select the target immutable version and confirm
**设为当前版本**. The operation only updates `workspace_agents.current_version`; it does not rebuild,
edit or delete a Release. Verify that a newly created task pins the selected version. Tasks and Sessions
created before the change must retain their original version. To roll forward again, select the newer
immutable version in the same drawer.

The equivalent API is
`POST /v1/agents/{agent_id}/versions/{version}/promote`. It is owner-scoped and records
`agent.promote` audit evidence. Do not implement rollback by editing `agent_versions`, changing a task's
pinned version or deleting a newer Release.

## Manual environment Agent rollback

1. Stop new promotion runs for the affected environment.
2. Identify the last healthy snapshot from the deployment audit record.
3. Confirm its Agent/package/image hashes still match signed release evidence.
4. Call the existing rollback endpoint with the current environment revision and a unique idempotency
   key, or rerun the failed workflow's rollback command from the same checked-out commit.
5. Wait for `succeeded`; create a new Session and verify it resolves the restored snapshot.
6. Verify a Session created before the rollback remains pinned to its original snapshot.

## Manual application image rollback

On the deployment runner:

```bash
uv run python scripts/deploy_release.py rollback \
  --compose-env "$HARNESS_DEPLOY_ENV_FILE" \
  --state-root "$HARNESS_RELEASE_STATE_ROOT" \
  --environment canary
```

The command pulls the previous digests and starts with `--no-build`. It does not automatically downgrade
PostgreSQL. Migrations must follow expand/contract: release N remains compatible with N-1 binaries until
N is proven in production and the rollback window closes. A destructive migration must be a separately
approved maintenance event with a tested backup restore, never an automatic CD step.

## Disaster recovery ownership

| Data | Authority | Recovery |
| --- | --- | --- |
| PostgreSQL | Sessions, Runs, Events, deployments, approvals, audit, memory metadata | PITR/base backup plus WAL; restore first |
| MinIO/S3 | inputs, workspaces, artifacts, reports | versioned bucket replication/backup; restore after DB |
| Redis | queues and visibility leases | disposable; restart and let PostgreSQL controllers reconcile |
| Langfuse | trace/score copy | restore independently; its outage must not change Run terminal state |
| Release state | current/previous signed manifests | back up with runner configuration; manifests contain no secrets |

## Recovery drill

1. Declare incident scope, freeze promotion and record current release/snapshot IDs.
2. Restore PostgreSQL to an isolated recovery environment; run schema head verification before traffic.
3. Restore the matching object-store version and verify representative artifact SHA-256 values.
4. Start Redis empty, then run controller/reaper reconciliation; confirm no terminal Run reopens.
5. Deploy the last signed production manifest with `--no-build`.
6. Verify login/RBAC, tenant isolation, one pinned Session, approval resume, cancellation, artifact download,
   Memory deletion and Langfuse degradation behavior.
7. Compare counts for Sessions/Runs/Artifacts/Audit entries and document any accepted RPO loss.
8. Reopen traffic gradually and retain the drill report.

RPO/RTO are business decisions and must be filled in by the platform owner before production launch.
The technical runbook does not fabricate targets that backup infrastructure has not proven.
