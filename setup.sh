#!/usr/bin/env bash
# Deploys the AI Job Search dashboard (backend + frontend + k8s manifests)
# using values from a config file (default: setup.env, copy from setup.env.example).
#
# Usage:
#   cp setup.env.example setup.env   # fill it in first
#   ./setup.sh                       # uses ./setup.env
#   ./setup.sh path/to/other.env     # or point at a different config file
#
# Idempotent: safe to re-run. Set BUILD_IMAGES=false in the config to skip
# the docker build/push step and just re-apply manifests.

set -euo pipefail

CONFIG_FILE="${1:-setup.env}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------- helpers --
info()  { echo -e "\033[1;34m==>\033[0m $*"; }
warn()  { echo -e "\033[1;33m!! \033[0m $*"; }
fail()  { echo -e "\033[1;31mxx \033[0m $*" >&2; exit 1; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "'$1' is required but not found in PATH."; }

# ------------------------------------------------------------------ setup --
[ -f "$CONFIG_FILE" ] || fail "Config file '$CONFIG_FILE' not found. Copy setup.env.example to setup.env and fill it in."

info "Loading config from $CONFIG_FILE"
set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

: "${DEPLOY_MODE:=registry}"
: "${NAMESPACE:?NAMESPACE is required in $CONFIG_FILE}"
: "${INGRESS_HOST:?INGRESS_HOST is required in $CONFIG_FILE}"
: "${GEMINI_API_KEY:?GEMINI_API_KEY is required in $CONFIG_FILE}"
: "${GEMINI_MODEL:?GEMINI_MODEL is required in $CONFIG_FILE}"
: "${IMAGE_TAG:=latest}"
GIT_REPO_URL="${GIT_REPO_URL:-}"
GIT_TOKEN="${GIT_TOKEN:-}"
BUILD_IMAGES="${BUILD_IMAGES:-true}"

case "$DEPLOY_MODE" in
  registry|local) ;;
  *) fail "DEPLOY_MODE must be 'registry' or 'local' (got '$DEPLOY_MODE')" ;;
esac

if [ "$GEMINI_API_KEY" = "your-free-gemini-key-here" ]; then
  fail "GEMINI_API_KEY still has the placeholder value — edit $CONFIG_FILE first."
fi
if [ -z "$GIT_REPO_URL" ]; then
  warn "GIT_REPO_URL is blank — scraping and the repo-based profile will be unavailable."
  warn "You'll need to paste your profile into the dashboard's Profile tab instead."
fi

if [ "$DEPLOY_MODE" = "registry" ]; then
  : "${REGISTRY:?REGISTRY is required in $CONFIG_FILE when DEPLOY_MODE=registry}"
  IMAGE_BACKEND="${REGISTRY}/ai-job-search-backend:${IMAGE_TAG}"
  IMAGE_FRONTEND="${REGISTRY}/ai-job-search-frontend:${IMAGE_TAG}"
  IMAGE_PULL_POLICY="Always"
else
  # local mode: no registry, images stay tagged locally and get imported
  # straight into k3s's containerd. IfNotPresent so kubelet never tries an
  # external pull for a name/tag that only exists on this node.
  IMAGE_BACKEND="ai-job-search-backend:${IMAGE_TAG}"
  IMAGE_FRONTEND="ai-job-search-frontend:${IMAGE_TAG}"
  IMAGE_PULL_POLICY="IfNotPresent"
  K3S_SSH_HOST="${K3S_SSH_HOST:-}"
  K3S_CTR_CMD="${K3S_CTR_CMD:-k3s ctr}"
fi

require_cmd kubectl
require_cmd python3
kubectl cluster-info >/dev/null 2>&1 || fail "kubectl can't reach a cluster. Check your kubeconfig / Rancher context."

# ------------------------------------------------------------ build/import --
if [ "$BUILD_IMAGES" = "true" ]; then
  require_cmd docker
  info "Building backend image: $IMAGE_BACKEND"
  docker build -t "$IMAGE_BACKEND" ./backend
  info "Building frontend image: $IMAGE_FRONTEND"
  docker build -t "$IMAGE_FRONTEND" ./frontend

  if [ "$DEPLOY_MODE" = "registry" ]; then
    info "Pushing backend image"
    docker push "$IMAGE_BACKEND"
    info "Pushing frontend image"
    docker push "$IMAGE_FRONTEND"
  else
    import_image() {
      local image="$1"
      info "Importing $image into k3s containerd (namespace k8s.io)"
      if [ -n "$K3S_SSH_HOST" ]; then
        docker save "$image" | ssh "$K3S_SSH_HOST" "sudo $K3S_CTR_CMD -n k8s.io images import -"
      else
        docker save "$image" | sudo $K3S_CTR_CMD -n k8s.io images import -
      fi
    }
    import_image "$IMAGE_BACKEND"
    import_image "$IMAGE_FRONTEND"
  fi
else
  info "BUILD_IMAGES=false — skipping build, using existing images:"
  echo "    $IMAGE_BACKEND"
  echo "    $IMAGE_FRONTEND"
fi

# --------------------------------------------------------- render templates --
RENDER_DIR="k8s/rendered"
info "Rendering manifests into $RENDER_DIR"
rm -rf "$RENDER_DIR"
mkdir -p "$RENDER_DIR"

REGISTRY="${REGISTRY:-}" IMAGE_TAG="$IMAGE_TAG" NAMESPACE="$NAMESPACE" INGRESS_HOST="$INGRESS_HOST" \
GIT_REPO_URL="$GIT_REPO_URL" GIT_TOKEN="$GIT_TOKEN" GEMINI_API_KEY="$GEMINI_API_KEY" \
GEMINI_MODEL="$GEMINI_MODEL" IMAGE_BACKEND="$IMAGE_BACKEND" IMAGE_FRONTEND="$IMAGE_FRONTEND" \
IMAGE_PULL_POLICY="$IMAGE_PULL_POLICY" \
python3 - "$RENDER_DIR" <<'PYEOF'
import os, re, sys, glob

out_dir = sys.argv[1]
placeholders = {
    "NAMESPACE": os.environ["NAMESPACE"],
    "INGRESS_HOST": os.environ["INGRESS_HOST"],
    "GEMINI_MODEL": os.environ["GEMINI_MODEL"],
    "GEMINI_API_KEY": os.environ["GEMINI_API_KEY"],
    "GIT_REPO_URL": os.environ.get("GIT_REPO_URL", ""),
    "GIT_TOKEN": os.environ.get("GIT_TOKEN", ""),
    "IMAGE_BACKEND": os.environ["IMAGE_BACKEND"],
    "IMAGE_FRONTEND": os.environ["IMAGE_FRONTEND"],
    "IMAGE_PULL_POLICY": os.environ["IMAGE_PULL_POLICY"],
}

for path in sorted(glob.glob("k8s/templates/*.yaml")):
    text = open(path).read()
    def sub(m):
        key = m.group(1)
        if key not in placeholders:
            raise SystemExit(f"Unknown placeholder {{{{{key}}}}} in {path}")
        return placeholders[key]
    rendered = re.sub(r"\{\{([A-Z_]+)\}\}", sub, text)
    remaining = re.findall(r"\{\{[A-Z_]+\}\}", rendered)
    if remaining:
        raise SystemExit(f"Unfilled placeholders {remaining} in {path}")
    dest = os.path.join(out_dir, os.path.basename(path))
    with open(dest, "w") as f:
        f.write(rendered)
    print(f"  rendered {dest}")
PYEOF

# ----------------------------------------------------------------- apply --
info "Applying manifests to namespace '$NAMESPACE'"
kubectl apply -f "$RENDER_DIR/00-namespace.yaml"
kubectl apply -f "$RENDER_DIR/01-secret.yaml"
kubectl apply -f "$RENDER_DIR/02-configmap.yaml"
kubectl apply -f "$RENDER_DIR/03-storage.yaml"
kubectl apply -f "$RENDER_DIR/04-backend.yaml"
kubectl apply -f "$RENDER_DIR/05-frontend.yaml"
kubectl apply -f "$RENDER_DIR/06-ingress.yaml"

info "Waiting for rollout..."
kubectl -n "$NAMESPACE" rollout status deploy/backend --timeout=180s || warn "Backend rollout didn't finish in time — check 'kubectl -n $NAMESPACE get pods'"
kubectl -n "$NAMESPACE" rollout status deploy/frontend --timeout=120s || warn "Frontend rollout didn't finish in time — check 'kubectl -n $NAMESPACE get pods'"

echo
info "Done. Next steps:"
echo "    kubectl -n $NAMESPACE get pods"
echo "    kubectl -n $NAMESPACE logs deploy/backend -c sync-repo   # repo clone / bun install output"
echo "    kubectl -n $NAMESPACE logs deploy/backend                # API logs"
echo
echo "    Open: http://$INGRESS_HOST  (make sure that host resolves to your node's IP)"
if [ "$DEPLOY_MODE" = "local" ]; then
  echo
  warn "DEPLOY_MODE=local: images only exist on the node you imported them to."
  warn "If a pod gets scheduled on a different node (multi-node cluster, or the"
  warn "node gets rebuilt), it'll fail to pull. Fine for a single-node k3s box;"
  warn "otherwise pin these Deployments to that node or switch to registry mode."
fi
