from __future__ import annotations

import json
import os

from room_store import DEFAULT_ROOM_RETENTION_DAYS, RoomStore, resolve_dsn


def run_retention_cleanup() -> int:
    retention_days = max(
        1,
        int(os.environ.get("POINTROOM_RETENTION_DAYS", DEFAULT_ROOM_RETENTION_DAYS)),
    )
    store = RoomStore(
        resolve_dsn(),
        retention_days,
        load_rooms=False,
    )
    try:
        removed_count = store.cleanup_expired()
        migrated_count = store.migrate_legacy_snapshots()
    finally:
        store.adapter.close()
    print(
        json.dumps(
            {
                "event": "retention_cleanup",
                "status": "success",
                "removed_rooms": removed_count,
                "migrated_rooms": migrated_count,
                "retention_days": retention_days,
            },
            separators=(",", ":"),
        )
    )
    return removed_count


if __name__ == "__main__":
    run_retention_cleanup()