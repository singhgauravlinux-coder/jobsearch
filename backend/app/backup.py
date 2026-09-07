"""In-app backup/restore for the sqlite database.

Snapshots are written to BACKUP_DIR (on the same PVC as the live DB, so no
extra volume needed) using sqlite3's online backup API — this produces a
consistent copy even if a request is mid-write, unlike a plain file copy.
The web UI lets you create, list, download, restore, delete, and upload
backups without needing kubectl access to the cluster.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from app.db import BACKUP_DIR, DB_PATH, engine

NAME_RE = re.compile(r"^jobsearch-\d{8}-\d{6}(-[a-zA-Z0-9]+)?\.db$")


class BackupError(RuntimeError):
    pass


def _safe_path(name: str) -> Path:
    """Resolve a backup filename, rejecting anything that isn't one of ours
    (prevents path traversal via a crafted name)."""
    if not NAME_RE.match(name):
        raise BackupError(f"Invalid backup filename: {name}")
    path = BACKUP_DIR / name
    if path.parent != BACKUP_DIR:
        raise BackupError(f"Invalid backup filename: {name}")
    return path


def create_backup(label: str | None = None) -> dict:
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{re.sub(r'[^a-zA-Z0-9]', '', label)[:20]}" if label else ""
    name = f"jobsearch-{stamp}{suffix}.db"
    dest_path = BACKUP_DIR / name

    # Online backup API: safe to run while the app is serving requests.
    src = sqlite3.connect(str(DB_PATH))
    try:
        dest = sqlite3.connect(str(dest_path))
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()

    return _describe(dest_path)


def list_backups() -> list[dict]:
    files = sorted(BACKUP_DIR.glob("jobsearch-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [_describe(f) for f in files]


def _describe(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "size_bytes": stat.st_size,
        "created_at": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
    }


describe_backup = _describe


def get_backup_path(name: str) -> Path:
    path = _safe_path(name)
    if not path.exists():
        raise BackupError(f"Backup '{name}' not found")
    return path


def delete_backup(name: str) -> None:
    path = get_backup_path(name)
    path.unlink()


def restore_from_path(source_path: Path) -> None:
    """Restore the live DB from a backup file (or an uploaded file) using
    the same online backup API, then dispose the connection pool so new
    requests open fresh connections against the restored data."""
    if not source_path.exists():
        raise BackupError(f"Source file not found: {source_path}")

    # Validate it's actually a sqlite file before touching the live DB.
    try:
        check = sqlite3.connect(str(source_path))
        check.execute("SELECT name FROM sqlite_master LIMIT 1")
        check.close()
    except sqlite3.DatabaseError as e:
        raise BackupError(f"Not a valid sqlite database: {e}")

    src = sqlite3.connect(str(source_path))
    try:
        dest = sqlite3.connect(str(DB_PATH))
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()

    engine.dispose()


def restore_from_backup(name: str) -> None:
    restore_from_path(get_backup_path(name))
