"""Backup the SQLite database to a destination directory.

Uses the SQLite online-backup API so it is safe to run while the app is live.
Writes a timestamped copy and prunes anything older than the retention window.

Intended to be run on a schedule (Task Scheduler on Windows, cron on Linux,
or a third docker service if you want it inside the same compose stack).

Usage:
    python scripts/backup_db.py --dest <path-to-backup-dir>
    python scripts/backup_db.py --dest <path> --retention-days 30

Environment fallback: BACKUP_DEST and BACKUP_RETENTION_DAYS.
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Resolve project paths the same way config.py does, without importing it
# (this script should run from a cron without Streamlit being available).
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
DB_PATH = PROJECT_ROOT / "data" / "calls.db"


def online_backup(src: Path, dest: Path) -> None:
    """Copy a live SQLite database safely using the backup API."""
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dest))
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def prune_old_backups(dest_dir: Path, retention_days: int) -> int:
    """Delete backups older than retention_days. Returns count pruned."""
    if retention_days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    pruned = 0
    for f in dest_dir.glob("calls-*.db"):
        try:
            if datetime.utcfromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
                pruned += 1
        except OSError:
            pass
    return pruned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        default=os.getenv("BACKUP_DEST"),
        help="Destination directory for backup files. Required.",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=int(os.getenv("BACKUP_RETENTION_DAYS", "30")),
        help="Delete backups older than this many days (0 = keep forever).",
    )
    args = parser.parse_args()

    if not args.dest:
        print("Error: --dest is required (or set BACKUP_DEST in environment).", file=sys.stderr)
        return 2

    if not DB_PATH.exists():
        print(f"Error: database not found at {DB_PATH}", file=sys.stderr)
        return 2

    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    dest_path = dest_dir / f"calls-{stamp}.db"

    print(f"Backing up {DB_PATH} -> {dest_path}")
    online_backup(DB_PATH, dest_path)

    size_mb = dest_path.stat().st_size / (1024 * 1024)
    print(f"Backup complete: {size_mb:.2f} MB")

    pruned = prune_old_backups(dest_dir, args.retention_days)
    if pruned:
        print(f"Pruned {pruned} backup(s) older than {args.retention_days} days")

    return 0


if __name__ == "__main__":
    sys.exit(main())
