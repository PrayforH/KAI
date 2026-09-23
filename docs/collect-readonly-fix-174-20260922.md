# Read-only collect fix on 174 — 2026-09-22

Deploy record for the sandbox-collect fix that killed every 174 run with an
input artifact at publish time.

## Symptom

Runs with input artifacts failed at `publish_artifact`:

```
ERROR:harness.worker.orchestrator:run execution failed ... error_type=PermissionError
message=[Errno 13] Permission denied: '/tmp/run_<id>-deferred-<rand>/inputs/original/<stable_id>-<name>'
```

Four runs failed on 2026-09-22 (04:09–06:43 CST): `run_ff8f9441…`,
`run_f92dd903…`, `run_1ac441b…`, `run_3a2f1318…` — the last one is the
「已脱敏案例库支撑综合研判智能体_方案V4」 task on 3501. Their V4 汇报版 HTML
was lost with the sandboxes (see 「V4 文件丢失」 below).

## Root cause

Not a mount-permission issue. Input artifacts are staged **read-only (0444)**
(`input_artifacts.py::_replace_read_only_file`) under `inputs/original/` in the
deferred workspace (`/tmp/run_<id>-deferred-<rand>/`). CubeSandbox runs through
the E2B provider, whose `collect()` mirrors the remote workspace back with an
in-place `local.write_bytes(content)`. The worker user (uid 10001 `harness`)
owns the staged file but is denied by the absent write bit, so collection died
on the first `inputs/original/...` entry — before `outputs/` was ever synced.

The rule was already fixed in `daytona.py` (chmod 0o600 before write) with a
comment naming read-only inputs, but `e2b.py`, `opensandbox.py` and
`kubernetes.py` never got it; only daytona had a regression test. 174 moved to
cube + deferred on 09-21, which exposed the gap.

## Fix (dd776bff)

One shared helper `replace_collected_file(target, content, mode=None)` in
`sandbox/base.py`: write a temporary sibling, preserve the mode, `os.replace`
over the target — needs only directory write permission and keeps the 0444
shape. All four backends now go through it; the daytona copy of the rule is
gone. Regression tests added for e2b/opensandbox/kubernetes
(`test_*_collect_overwrites_read_only_staged_input`); the full sandbox suite
passes (171 in the develop worktree). Branch `fix/develop-review-20260921`,
based on `origin/develop` 21488020.

## Deploy

- Image: `harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:develop-20260922-dd776bff`
  (digest `sha256:5561477a…`), built from the fix branch by
  `scripts/build_harbor_174.sh` (api only; web unchanged).
- Image verified before deploy: extracted `e2b.py`/`base.py` from the image via
  `docker create` + `docker cp` and diffed against the fix-branch source —
  byte-identical.
- 174: pinned the new tag for `api` + `worker` in
  `compose.deepagents-174.yaml` (backup `compose.deepagents-174.yaml.bak-20260922-151025`),
  then `up-deepagents-174.sh`. api + 3 workers healthy on the new tag;
  3301/3501 web containers untouched.
- No non-terminal runs existed before or after the recreate.

Gotcha: `docker manifest inspect` on 174 always answers `no such manifest`,
even for tags it has already pulled — the working gate is `docker pull`
(9.5s for this tag; the registry is LAN-fast).

## Verification

E2E on 174 (`session_0f9862e8…`, `run_4660e421…`): uploaded a markdown input
artifact, ran with `input_artifact_ids`, prompt told the agent to read the
input and publish `outputs/summary.md`. Run **succeeded**; artifact
`summary.md` (85 B) delivered with the exact title line from the input. Worker
log shows the identical file flow as the failures — `POST inputs/original/…`
(upload), `GET inputs/original/…` 200 (collect) — followed by `artifact.ready`
instead of `PermissionError`.

## V4 文件丢失

The 汇报版 HTML from `run_3a2f1318…` is unrecoverable: worker-local
`/tmp/run_*` dirs are removed at teardown, the surviving sandbox's
`/home/user/harness` is empty, and the four failed runs have 0 artifact rows.
The V4 input still exists; re-running the task after this deploy will produce
it. Earlier versions did publish fine (V3 讲解手册 114 KB on 09-20), confirming
the regression arrived with cube+deferred on 09-21.

## Rollback

Revert the two `agent-studio-api:` lines in `compose.deepagents-174.yaml` to
`develop-20260921-0523fc10` and re-run `up-deepagents-174.sh`.
