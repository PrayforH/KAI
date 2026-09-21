# Sidebar submenus and Redis 8.10 — 174 verification

## Changes

- Reduce the space between the project list and the task heading by 14 px while preserving task row hit targets and indentation.
- Task actions now expose one “移入项目” entry. Its submenu lists other projects and, when applicable, “移出当前项目”.
- Account “主题外观” opens a submenu containing the existing dark and light choices.
- Submenus open on hover or click, allow mouse travel between panels, and stay within the viewport. Arrow keys navigate options; Escape returns to the parent menu.
- Pin development, Harbor, web-79 and CI Redis images to `8.10.1-alpine`.

## Redis version and deployment

On 2026-09-21, the [official Docker image definition](https://raw.githubusercontent.com/docker-library/official-images/master/library/redis) maps the `8.10` series to **8.10.1**. Although the source download index contains 8.10.2, its official Docker tag was not available when checked. Use the available official image rather than an unverified image tag.

- Official multi-platform index: `sha256:bd999b5cfee25fb24b8320a31fddbd69f462df44c8138c66e369582937beebc0`.
- Mirrored amd64 image: `harbor.shdata.com:5000/dependencies-ai/redis:8.10.1-alpine`.
- Harbor manifest: `sha256:2f4092145274cb2189d42a19cc62dd67997ba4ddbdafc5c34b0029f98b0fd9fd`.
- 174 container: `agent-studio-174-redis-1`; previous runtime was 7.0.15.
- Persisted `HARNESS_HARBOR_REDIS_TAG=8.10.1-alpine` in the existing production environment file. Only the Redis service was recreated; existing API/worker images were retained.
- The web-79 file is updated for future deployments; no service on 79 was changed during this verification.

The upgrade first loaded a production RDB copy into isolated Redis 7 and Redis 8 containers. All 877 keys (sorted sets) had identical semantic content. With no active runs, the API, three workers and quality-sync were stopped, Redis was stopped cleanly, and its full data directory (including AOF) was backed up. After recreating Redis, a second comparison against the old-version AOF copy passed before application writers restarted. AOF remains enabled and healthy.

Backups on 174 are under `/data/submenus-redis-20260921/`:

- `redis-data-before/`: complete Redis 7 data directory.
- `redis7-data-backup.tar.gz`: archived copy, SHA-256 `60108dfa8457d14ae74a9f7b4b42a47d001060ee25c7f7d1632835ba585e5bc9`.
- `env.production.before`: original production environment, access restricted; contains secrets and must not be committed.

For rollback, first stop application writers after checking active runs, stop Redis 8, preserve its current data separately, restore the **complete** Redis 7 data directory to the existing `agent-studio-174_redis-data` volume, and restore the previous Redis image setting. Recreate only Redis using the existing production Compose files, validate data and health, then restart the same application containers. Do not start Redis 7 on data that Redis 8 has rewritten. Restoring the pre-upgrade backup after new writes have been accepted requires reconciliation of those writes.

## Validation

- Frontend: 763 passed, 1 skipped; TypeScript check passed.
- Production frontend image build passed; main/Harbor Compose validation passed. Web-79 Compose structure validated with `--no-env-resolution` because its machine-specific `model.env` is not in the repository.
- Redis integration: 12 passed against Redis 8.10.1, covering queue retries/leases, event replay, cancellation and recovery.
- Browser: project submenu hover travel, Escape preserving the parent, dark/light switching and viewport positioning passed with no page errors or API failures.
- 174: Redis, API, three workers and quality-sync healthy after restart; 877 keys retained; AOF write status `ok`.

The frontend image is `kai/axis-web:submenus-20260921`, served by `axis-web-submenus-20260921` on port 3501. The previous web container is retained as `axis-web-sidebar-scroll-20260921-rollback-submenus` for rollback.
