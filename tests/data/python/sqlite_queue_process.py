"""Independent processes opening and claiming only generated public SQLite queue adapters."""

from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime


def main() -> None:
    """Open the explicit public adapter, signal readiness, and claim after the parent's input barrier."""
    source, models, package, path, now, until = sys.argv[1:]
    sys.path[:0] = [source, models]
    protocols = importlib.import_module(f"{package}.protocols")
    store = protocols.SQLiteQueueStore(path)
    try:
        store.get("missing")
        sys.stdout.write("ready\n")
        sys.stdout.flush()
        sys.stdin.readline()
        leases = store.claim(now=datetime.fromisoformat(now), lease_until=datetime.fromisoformat(until), limit=2)
        sys.stdout.write(json.dumps([[lease.entry.entry_id, lease.entry.version] for lease in leases]) + "\n")
        sys.stdout.flush()
    finally:
        store.close()


if __name__ == "__main__":
    main()
