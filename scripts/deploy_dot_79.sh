#!/usr/bin/env bash
# One-shot: rebuild the API image from the running r21 base, roll it to
# control/api/worker, then re-run the real acceptance. Idempotent.
#
# Requires an authorised SSH channel to 79 (see the private deploy notes);
# this script never stores credentials and never prints key material.
set -euo pipefail
TAG=${1:-dot5}
WORKTREE=${WORKTREE:-/Users/xiaokai/.codex/worktrees/kai-dot-20261028/agent studio}
REMOTE=${REMOTE:-root@172.20.105.79}
CTX=/data/kai-dot-20261028
COMPOSE_DIR=/data/kai-79-20261013

echo "== 1/5 transfer source"
cd "$WORKTREE"
export COPYFILE_DISABLE=1
tar --no-xattrs --exclude='._*' --exclude='__pycache__' --exclude='*.pyc' -czf - src/harness migrations \
  | ssh "$REMOTE" "mkdir -p $CTX/ctx && tar -xzf - -C $CTX/ctx"

echo "== 2/5 build image"
ssh "$REMOTE" "cat > $CTX/api.Dockerfile <<'DOCKER'
FROM kai/assistant-api:20261027-r21-computer-transfer
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-client && rm -rf /var/lib/apt/lists/*
COPY src/harness /app/project/lib/python3.12/site-packages/harness
COPY migrations /app/migrations
DOCKER
cd $CTX/ctx && docker build -q -f $CTX/api.Dockerfile -t kai/assistant-api:$TAG . >/dev/null
docker run --rm --entrypoint python kai/assistant-api:$TAG -c 'from harness.api.app import create_app; print(\"image ok\")'"

echo "== 3/5 roll services (control, api, worker)"
ssh "$REMOTE" "cd $COMPOSE_DIR && python3 - <<PY
import json, os
p='compose.private.json'; d=json.load(open(p))
for s in ('api','worker','computer-control'): d['services'][s]['image']='kai/assistant-api:$TAG'
open(p,'w').write(json.dumps(d, indent=2)+'\n'); os.chmod(p,0o600)
PY
docker compose -f compose.private.json config --quiet
docker compose -f compose.private.json up -d --no-deps computer-control api worker"

echo "== 4/5 health"
ssh "$REMOTE" "sleep 25; docker ps --filter name=kai-79-20261013 --format '{{.Names}}\t{{.Status}}' | grep -E 'api-1|worker-1|control-1'"

echo "== 5/5 real acceptance (desktop lease + model run)"
ssh "$REMOTE" "docker cp $CTX/accept.py kai-79-20261013-api-1:/tmp/accept.py && docker exec -e DOT_TOKEN=DELTA\$(date +%H%M%S) kai-79-20261013-api-1 python /tmp/accept.py 2>&1 | grep -vE 'INFO|HTTP Request' | tail -25"
ssh "$REMOTE" "docker logs --tail 40 kai-79-20261013-computer-control-1 2>&1 | grep -i 'desktop action failed' | tail -5 || true"
