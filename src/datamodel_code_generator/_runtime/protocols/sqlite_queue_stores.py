"""Explicit bounded SQLite queue stores with atomic claims, recovery, versioned writes, and terminal purge."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Literal, TypeAlias, TypeVar
from uuid import uuid4

from .errors import QueueFullError, QueueStoreError
from .options import positive_count
from .queue_codec import decode, encode
from .queue_state import entry_value, instant, recover
from .queues import TERMINAL_STATES, QueueEntry, QueueLease
from .sqlite import SQLiteConnection
from .sqlite_worker import SQLiteWorker

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Iterator
    from datetime import datetime
    from pathlib import Path

__all__ = ("AsyncSQLiteQueueStore", "SQLiteQueueStore")

T = TypeVar("T")
_Action: TypeAlias = Literal["put", "get", "claim", "compare_exchange", "purge_terminal", "close"]


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    finally:
        if connection.in_transaction:
            connection.rollback()


def _row(row: tuple[object, ...], action: _Action) -> QueueEntry:
    entry_id = row[0]
    try:
        return _decoded_row(row)
    except Exception as error:
        raise QueueStoreError(
            action=action, entry_id=entry_id if isinstance(entry_id, str) else None, cause=error
        ) from error


def _decoded_row(row: tuple[object, ...]) -> QueueEntry:
    entry_id, text, size = row
    if not isinstance(entry_id, str) or not isinstance(text, str) or not isinstance(size, int):
        msg = "invalid SQLite queue row"
        raise TypeError(msg)
    entry = decode(text)
    if entry.entry_id != entry_id or len(entry.payload) != size:
        msg = "inconsistent SQLite queue row"
        raise ValueError(msg)
    return entry


def _write(connection: sqlite3.Connection, entry: QueueEntry) -> None:
    connection.execute(
        "UPDATE queue_entries SET record = ?, payload_bytes = ? WHERE entry_id = ?",
        (encode(entry), len(entry.payload), entry.entry_id),
    )


class _QueueDatabase:
    """Database transactions for one adapter's finite queue capacities."""

    def __init__(self, max_entries: int, max_bytes: int) -> None:
        positive_count(max_entries, "max_entries")
        positive_count(max_bytes, "max_bytes")
        self.max_entries, self.max_bytes = max_entries, max_bytes

    def _capacity(self, connection: sqlite3.Connection, size: int, action: str, entry_id: str) -> None:
        count, total = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(payload_bytes), 0) FROM queue_entries"
        ).fetchone()
        if action == "put" and count >= self.max_entries:
            raise QueueFullError(kind="entries", limit=self.max_entries, observed=count + 1, entry_id=entry_id)
        if (observed := total + size) > self.max_bytes:
            raise QueueFullError(
                kind="bytes",
                limit=self.max_bytes,
                observed=observed,
                action="put" if action == "put" else "compare_exchange",
                entry_id=entry_id,
            )

    def put(self, connection: sqlite3.Connection, entry: QueueEntry) -> None:
        with _transaction(connection):
            if self.get(connection, entry.entry_id) is not None:
                raise QueueStoreError(action="put", entry_id=entry.entry_id)
            self._capacity(connection, len(entry.payload), "put", entry.entry_id)
            stored = replace(entry, version=uuid4().hex)
            connection.execute(
                "INSERT INTO queue_entries VALUES (?, ?, ?)", (stored.entry_id, encode(stored), len(stored.payload))
            )

    @staticmethod
    def get(connection: sqlite3.Connection, entry_id: str) -> QueueEntry | None:
        row = connection.execute(
            "SELECT entry_id, record, payload_bytes FROM queue_entries WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        return None if row is None else _row(row, "get")

    @staticmethod
    def claim(
        connection: sqlite3.Connection, *, now: datetime, lease_until: datetime, limit: int
    ) -> tuple[QueueLease, ...]:
        with _transaction(connection):
            rows = connection.execute("SELECT entry_id, record, payload_bytes FROM queue_entries").fetchall()
            entries: list[QueueEntry] = []
            for row in rows:
                entry = _row(row, "claim")
                recovered = recover(entry, now)
                if recovered is not entry:
                    _write(connection, recovered)
                entries.append(recovered)
            ready = sorted(
                (entry for entry in entries if entry.state == "pending" and entry.not_before <= now),
                key=lambda entry: (entry.created_at, entry.entry_id),
            )[:limit]
            leases: list[QueueLease] = []
            for entry in ready:
                lease_id = uuid4().hex
                leased = replace(entry, state="leased", lease_id=lease_id, lease_until=lease_until, version=uuid4().hex)
                _write(connection, leased)
                leases.append(QueueLease(entry=leased, lease_id=lease_id))
            return tuple(leases)

    def compare_exchange(
        self, connection: sqlite3.Connection, entry_id: str, expected_version: str, entry: QueueEntry
    ) -> bool:
        with _transaction(connection):
            if (current := self.get(connection, entry_id)) is None or current.version != expected_version:
                return False
            if entry.state == "leased" and current.state == "leased" and entry.lease_id != current.lease_id:
                return False
            self._capacity(connection, len(entry.payload) - len(current.payload), "compare_exchange", entry_id)
            _write(connection, replace(entry, version=uuid4().hex))
            return True

    @staticmethod
    def purge_terminal(connection: sqlite3.Connection, before: datetime) -> tuple[QueueEntry, ...]:
        with _transaction(connection):
            rows = connection.execute("SELECT entry_id, record, payload_bytes FROM queue_entries").fetchall()
            entries = (_row(row, "purge_terminal") for row in rows)
            purged = sorted(
                (entry for entry in entries if entry.state in TERMINAL_STATES and entry.created_at < before),
                key=lambda entry: (entry.created_at, entry.entry_id),
            )
            connection.executemany(
                "DELETE FROM queue_entries WHERE entry_id = ?", ((entry.entry_id,) for entry in purged)
            )
            return tuple(purged)


class SQLiteQueueStore:
    """A lazy explicit SQLite queue store; terminal rows count toward both capacities until purged.

    The database uses schema version 1, WAL and a five-second busy timeout. Each operation uses the same locked
    connection. Other stores and processes can safely claim disjoint ordered entries from the same explicit path.
    """

    __slots__ = ("_connection", "_database")

    def __init__(self, path: str | Path, *, max_entries: int = 10000, max_bytes: int = 268435456) -> None:
        """Retain the path and capacity limits without creating a database."""
        self._database = _QueueDatabase(max_entries, max_bytes)
        self._connection = SQLiteConnection(path)

    def _run(self, action: _Action, entry_id: str | None, operation: Callable[[sqlite3.Connection], T]) -> T:
        try:
            return self._connection.run(operation)
        except QueueStoreError:
            raise
        except Exception as error:
            raise QueueStoreError(action=action, entry_id=entry_id, cause=error) from error

    def put(self, entry: QueueEntry) -> None:
        """Store a new entry atomically, refusing duplicate identifiers and either capacity overflow."""
        entry = entry_value(entry)
        self._run("put", entry.entry_id, partial(self._database.put, entry=entry))

    def get(self, entry_id: str) -> QueueEntry | None:
        """Return the complete stored record with this identifier, or None."""
        return self._run("get", entry_id, partial(self._database.get, entry_id=entry_id))

    def claim(self, *, now: datetime, lease_until: datetime, limit: int) -> tuple[QueueLease, ...]:
        """Atomically recover expired leases and claim ready entries in creation-time and ID order."""
        positive_count(limit, "limit")
        now, lease_until = instant(now, "now"), instant(lease_until, "lease_until")
        return self._run("claim", None, partial(self._database.claim, now=now, lease_until=lease_until, limit=limit))

    def compare_exchange(self, entry_id: str, expected_version: str, entry: QueueEntry) -> bool:
        """Replace only the matching version and lease, refusing payload growth beyond capacity."""
        entry = entry_value(entry)
        if entry.entry_id != entry_id:
            raise QueueStoreError(action="compare_exchange", entry_id=entry_id)
        return self._run(
            "compare_exchange",
            entry_id,
            partial(self._database.compare_exchange, entry_id=entry_id, expected_version=expected_version, entry=entry),
        )

    def purge_terminal(self, before: datetime) -> tuple[QueueEntry, ...]:
        """Atomically remove and return exactly the terminal records created strictly before this instant."""
        return self._run(
            "purge_terminal", None, partial(self._database.purge_terminal, before=instant(before, "before"))
        )

    def close(self) -> None:
        """Close the locked connection once; closing an unused store creates no database."""
        try:
            self._connection.close()
        except Exception as error:
            raise QueueStoreError(action="close", cause=error) from error


class AsyncSQLiteQueueStore:
    """An explicit native-async SQLite queue adapter with one lazy worker and 64 pending FIFO jobs."""

    __slots__ = ("_database", "_worker")

    def __init__(self, path: str | Path, *, max_entries: int = 10000, max_bytes: int = 268435456) -> None:
        """Retain the path and capacities without creating a file, thread, or connection."""
        self._database = _QueueDatabase(max_entries, max_bytes)
        self._worker = SQLiteWorker(path)

    async def _run(self, action: _Action, entry_id: str | None, operation: Callable[[sqlite3.Connection], T]) -> T:
        try:
            return await self._worker.run(operation)
        except QueueStoreError:
            raise
        except Exception as error:
            raise QueueStoreError(action=action, entry_id=entry_id, cause=error) from error

    async def put(self, entry: QueueEntry) -> None:
        """Store a new entry atomically, with bounded async backpressure and no HTTP worker work."""
        entry = entry_value(entry)
        await self._run("put", entry.entry_id, partial(self._database.put, entry=entry))

    async def get(self, entry_id: str) -> QueueEntry | None:
        """Return the complete stored record with this identifier, or None."""
        return await self._run("get", entry_id, partial(self._database.get, entry_id=entry_id))

    async def claim(self, *, now: datetime, lease_until: datetime, limit: int) -> tuple[QueueLease, ...]:
        """Atomically recover expired leases and claim ready entries in creation-time and ID order."""
        positive_count(limit, "limit")
        now, lease_until = instant(now, "now"), instant(lease_until, "lease_until")
        return await self._run(
            "claim", None, partial(self._database.claim, now=now, lease_until=lease_until, limit=limit)
        )

    async def compare_exchange(self, entry_id: str, expected_version: str, entry: QueueEntry) -> bool:
        """Replace only the matching version and lease, refusing payload growth beyond capacity."""
        entry = entry_value(entry)
        if entry.entry_id != entry_id:
            raise QueueStoreError(action="compare_exchange", entry_id=entry_id)
        return await self._run(
            "compare_exchange",
            entry_id,
            partial(self._database.compare_exchange, entry_id=entry_id, expected_version=expected_version, entry=entry),
        )

    async def purge_terminal(self, before: datetime) -> tuple[QueueEntry, ...]:
        """Atomically remove and return exactly the terminal records created strictly before this instant."""
        return await self._run(
            "purge_terminal", None, partial(self._database.purge_terminal, before=instant(before, "before"))
        )

    async def aclose(self) -> None:
        """Close admission, drain accepted work, and share retained settlement across cancelled or repeated closes."""
        try:
            await self._worker.aclose()
        except Exception as error:
            raise QueueStoreError(action="close", cause=error) from error
