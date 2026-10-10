#!/usr/bin/env bash
set -euo pipefail

# The verified upstream binary binds this source revision to its release.
upstream_info="$("${UPSTREAM_KUBECTL:-/usr/local/bin/upstream-kubectl}" version --client -o json)"
printf '%s\n' "$upstream_info" | grep -F "\"gitCommit\": \"$KUBECTL_SOURCE_COMMIT\"" >/dev/null
printf '%s\n' "$upstream_info" | grep -F "\"gitVersion\": \"$KUBECTL_VERSION\"" >/dev/null

mkdir -p /kubectl-source /out
cd /kubectl-source
git init
git remote add origin https://github.com/kubernetes/kubernetes.git
git -c http.version=HTTP/1.1 fetch --depth=1 origin "$KUBECTL_SOURCE_COMMIT"
git checkout --detach FETCH_HEAD
test "$(git rev-parse HEAD)" = "$KUBECTL_SOURCE_COMMIT"

# Upgrade only the vulnerable module and its required dependency closure.
# GOSUMDB keeps module content verification enabled; no private-module bypass.
export GOTOOLCHAIN=local CGO_ENABLED=0
GOFLAGS=-mod=mod go get "golang.org/x/net@v$KUBECTL_NET_VERSION"
go work vendor
version_package=k8s.io/component-base/version
build_version="$KUBECTL_VERSION+kai.netfix.1"
go build -trimpath -buildvcs=false \
  -ldflags "-s -w -X $version_package.gitMajor=1 -X $version_package.gitMinor=36 -X $version_package.gitVersion=$build_version -X $version_package.gitCommit=$KUBECTL_SOURCE_COMMIT -X $version_package.gitTreeState=dirty -X $version_package.buildDate=2026-10-10T00:00:00Z" \
  -o /out/kubectl ./cmd/kubectl
go version -m /out/kubectl | grep -E "golang.org/x/net[[:space:]]+v$KUBECTL_NET_VERSION[[:space:]]" >/dev/null
/out/kubectl version --client -o json > /out/version.json
grep -F "\"gitVersion\": \"$build_version\"" /out/version.json >/dev/null
grep -F "\"gitCommit\": \"$KUBECTL_SOURCE_COMMIT\"" /out/version.json >/dev/null
