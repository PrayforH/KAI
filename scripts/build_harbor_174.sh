#!/usr/bin/env bash
# Build and push immutable API/Web images for the 174 gray environment.
# The existing inline cache preserves the large, stable dependency layers.
# Each build publishes exactly one immutable tag: Harbor has been observed to
# commit one tag and stall or drop the other when a single push carries both
# an immutable tag and a mutable cache alias.

set -euo pipefail

TAG="${1:-}"
REGISTRY="${HARNESS_HARBOR_REGISTRY:-harbor.shdata.com:5000}"
PROJECT="${HARNESS_HARBOR_PROJECT:-agent-studio}"
ARCH="${HARNESS_IMAGE_ARCH:-amd64}"
PLATFORM="${HARNESS_BUILD_PLATFORM:-linux/${ARCH}}"
COMPONENTS="${HARNESS_BUILD_COMPONENTS:-api web}"
BUILDER="${HARNESS_BUILDX_BUILDER:-}"
PROGRESS="${HARNESS_BUILDKIT_PROGRESS:-plain}"
ATTESTATIONS="${HARNESS_BUILD_ATTESTATIONS:-auto}"
KUBECTL_IMAGE="${KUBECTL_IMAGE:-cgr.dev/chainguard/kubectl@sha256:1e1aa9dedf0d9008e5a3710b23f2072bc2ab83117146d503c689b5d2592add3d}"

log() { printf '[build-174] %s\n' "$*"; }
die() { log "ERROR: $*"; exit 1; }

[[ "${TAG}" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] \
  || die "usage: build_harbor_174.sh <immutable-image-tag>"
command -v docker >/dev/null 2>&1 || die "docker is required"
docker buildx version >/dev/null 2>&1 || die "docker buildx is required"

if [ "${ATTESTATIONS}" = "auto" ]; then
  # Consume the complete buildx output. Exiting awk on the first match makes
  # docker receive SIGPIPE, which becomes a false exit 255 under pipefail.
  BUILDX_DRIVER="$(docker buildx inspect 2>/dev/null | awk '/^Driver:/ && !driver { driver=$2 } END { print driver }')"
  if [ "${BUILDX_DRIVER}" = "docker" ]; then
    ATTESTATIONS=false
    log "docker buildx driver does not support attestations; building without SBOM/provenance"
  else
    ATTESTATIONS=true
  fi
fi
case "${ATTESTATIONS}" in
  true|false) ;;
  *) die "HARNESS_BUILD_ATTESTATIONS must be auto, true, or false" ;;
esac

REPOSITORY_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
  || die "run this script from the Agent Studio repository"
SOURCE_COMMIT="$(cd "${REPOSITORY_ROOT}" && git rev-parse HEAD)"
if [ -n "$(cd "${REPOSITORY_ROOT}" && git status --porcelain --untracked-files=normal)" ]; then
  [ "${HARNESS_ALLOW_DIRTY_BUILD:-false}" = "true" ] \
    || die "worktree is dirty; commit first or set HARNESS_ALLOW_DIRTY_BUILD=true for gray testing"
  SOURCE_STATE="dirty"
else
  SOURCE_STATE="clean"
fi

METADATA_ROOT="${HARNESS_BUILD_METADATA_DIR:-${REPOSITORY_ROOT}/dist/harbor-build/${TAG}}"
mkdir -p "${METADATA_ROOT}"
IMAGE_PREFIX="${REGISTRY}/${PROJECT}/${ARCH}"

build_component() {
  local component="$1" dockerfile image_repository image cache_ref metadata_file started elapsed
  case "${component}" in
    api)
      dockerfile="deploy/docker/api.Dockerfile"
      image_repository="${IMAGE_PREFIX}/agent-studio-api"
      ;;
    web)
      dockerfile="deploy/docker/web.Dockerfile"
      image_repository="${IMAGE_PREFIX}/agent-studio-web"
      ;;
    *)
      die "unsupported component: ${component}; expected api or web"
      ;;
  esac

  image="${image_repository}:${TAG}"
  # Read the last known inline cache in the same repository. Do not publish
  # this mutable alias together with the release tag; deployment correctness
  # must never depend on a best-effort cache pointer.
  cache_ref="${image_repository}:buildcache-${ARCH}"
  metadata_file="${METADATA_ROOT}/${component}.json"
  started="$(date +%s)"
  log "building ${component} -> ${image} (source=${SOURCE_COMMIT}, ${SOURCE_STATE})"

  local command=(
    docker buildx build
    --platform "${PLATFORM}"
    --file "${dockerfile}"
    --tag "${image}"
    --label "org.opencontainers.image.revision=${SOURCE_COMMIT}"
    --label "org.opencontainers.image.source-state=${SOURCE_STATE}"
    --cache-from "type=registry,ref=${cache_ref}"
    --cache-to "type=inline"
    --metadata-file "${metadata_file}"
    --progress "${PROGRESS}"
    --push
  )
  if [ "${ATTESTATIONS}" = "true" ]; then
    command+=(--provenance=mode=max --sbom=true)
  else
    # Buildx otherwise emits a minimal provenance attestation by default even
    # with the Docker driver. Older/private registries can accept every blob
    # and then stall while committing that manifest list, so disable both
    # attestations explicitly for gray-environment images.
    command+=(--provenance=false --sbom=false)
  fi
  if [ -n "${BUILDER}" ]; then
    command+=(--builder "${BUILDER}")
  fi
  if [ "${component}" = "api" ]; then
    command+=(--build-arg "KUBECTL_IMAGE=${KUBECTL_IMAGE}")
  fi
  command+=("${REPOSITORY_ROOT}")
  "${command[@]}"

  elapsed="$(( $(date +%s) - started ))"
  log "pushed ${image} in ${elapsed}s; metadata=${metadata_file}"
}

for component in ${COMPONENTS}; do
  build_component "${component}"
done

log "build complete; deploy with: bash scripts/deploy_174.sh upgrade ${TAG}"
