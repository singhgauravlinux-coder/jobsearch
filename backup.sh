#!/usr/bin/env bash
# Copies the backend's live sqlite DB out of the cluster to ./backups/ on
# your machine. This is the real backup — the "Export data" button in the
# dashboard gives you a JSON snapshot for reading/portability, but this file
# is what restore.sh needs to put you back exactly where you were.
#
# Usage:
#   ./backup.sh              # uses NAMESPACE from setup.env if present, else ai-job-search
#   ./backup.sh my-namespace

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

NAMESPACE="${1:-}"
if [ -z "$NAMESPACE" ] && [ -f setup.env ]; then
  NAMESPACE="$(grep -E '^NAMESPACE=' setup.env | cut -d= -f2-)"
fi
NAMESPACE="${NAMESPACE:-ai-job-search}"

command -v kubectl >/dev/null 2>&1 || { echo "kubectl not found in PATH" >&2; exit 1; }

POD="$(kubectl -n "$NAMESPACE" get pod -l app=ai-job-search-backend -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [ -z "$POD" ]; then
  echo "No running backend pod found in namespace '$NAMESPACE'." >&2
  echo "Check: kubectl -n $NAMESPACE get pods" >&2
  exit 1
fi

mkdir -p backups
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="backups/jobsearch-${STAMP}.db"

echo "==> Copying /data/jobsearch.db from pod $POD (namespace $NAMESPACE)"
kubectl -n "$NAMESPACE" cp "${POD}:/data/jobsearch.db" "$DEST"

SIZE="$(du -h "$DEST" | cut -f1)"
echo "==> Saved $DEST ($SIZE)"
echo
echo "To restore this later: ./restore.sh $DEST"
