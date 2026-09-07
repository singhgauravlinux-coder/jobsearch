#!/usr/bin/env bash
# Restores a ./backups/*.db file (made by backup.sh) back into the backend's
# volume. This overwrites whatever's currently in the database — it asks for
# confirmation first.
#
# Usage:
#   ./restore.sh backups/jobsearch-20260903-030000.db
#   ./restore.sh backups/jobsearch-20260903-030000.db my-namespace

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BACKUP_FILE="${1:-}"
[ -n "$BACKUP_FILE" ] || { echo "Usage: ./restore.sh <backup-file.db> [namespace]" >&2; exit 1; }
[ -f "$BACKUP_FILE" ] || { echo "File not found: $BACKUP_FILE" >&2; exit 1; }

NAMESPACE="${2:-}"
if [ -z "$NAMESPACE" ] && [ -f setup.env ]; then
  NAMESPACE="$(grep -E '^NAMESPACE=' setup.env | cut -d= -f2-)"
fi
NAMESPACE="${NAMESPACE:-ai-job-search}"

command -v kubectl >/dev/null 2>&1 || { echo "kubectl not found in PATH" >&2; exit 1; }

POD="$(kubectl -n "$NAMESPACE" get pod -l app=ai-job-search-backend -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [ -z "$POD" ]; then
  echo "No running backend pod found in namespace '$NAMESPACE'." >&2
  exit 1
fi

echo "This will OVERWRITE the live database in namespace '$NAMESPACE' (pod $POD)"
echo "with the contents of: $BACKUP_FILE"
read -r -p "Type 'yes' to continue: " CONFIRM
[ "$CONFIRM" = "yes" ] || { echo "Aborted."; exit 1; }

echo "==> Copying $BACKUP_FILE into pod $POD:/data/jobsearch.db"
kubectl -n "$NAMESPACE" cp "$BACKUP_FILE" "${POD}:/data/jobsearch.db"

echo "==> Restarting backend so it picks up the restored file cleanly"
kubectl -n "$NAMESPACE" rollout restart deploy/backend
kubectl -n "$NAMESPACE" rollout status deploy/backend --timeout=120s

echo "==> Done. Restored from $BACKUP_FILE"
