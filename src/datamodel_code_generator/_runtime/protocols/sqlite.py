"""The private version-one database family shared by explicitly constructed SQLite stores."""

from __future__ import annotations

import sqlite3
from threading import Lock
from typing import TYPE_CHECKING, Final, TypeVar
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

T = TypeVar("T")

_SCHEMA: Final = {
    "store_meta": (
        "CREATE TABLE store_meta (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
        "namespace TEXT NOT NULL, last_lease INTEGER NOT NULL CHECK (last_lease >= 0))"
    ),
    "queue_entries": (
        "CREATE TABLE queue_entries (entry_id TEXT PRIMARY KEY NOT NULL, record TEXT NOT NULL, "
        "payload_bytes INTEGER NOT NULL CHECK (payload_bytes >= 0))"
    ),
    "blob_content": (
        "CREATE TABLE blob_content (content_id INTEGER PRIMARY KEY, body BLOB NOT NULL, "
        "size INTEGER NOT NULL CHECK (size >= 0), sha256 BLOB NOT NULL)"
    ),
    "blob_refs": (
        "CREATE TABLE blob_refs (key TEXT PRIMARY KEY NOT NULL, "
        "content_id INTEGER NOT NULL UNIQUE REFERENCES blob_content(content_id))"
    ),
}


def _compatible(connection: sqlite3.Connection) -> bool:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    objects = connection.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    if version == 0 and not objects:
        return False
    if version != 1 or {name: sql for kind, name, sql in objects if kind == "table"} != _SCHEMA:
        msg = "unsupported SQLite store schema"
        raise ValueError(msg)
    if any(kind != "table" for kind, _, _ in objects):
        msg = "unexpected SQLite store objects"
        raise ValueError(msg)
    rows = connection.execute("SELECT singleton, namespace, last_lease FROM store_meta").fetchall()
    if len(rows) != 1 or rows[0][0] != 1:
        msg = "invalid SQLite store metadata row"
        raise ValueError(msg)
    if (
        not isinstance(rows[0][1], str)
        or not rows[0][1]
        or not isinstance(rows[0][2], int)
        or not 0 <= rows[0][2] <= 2**63 - 1
    ):
        msg = "invalid SQLite store namespace or counter"
        raise ValueError(msg)
    return True


def _initialize(connection: sqlite3.Connection) -> None:
    if not _compatible(connection):
        connection.execute("BEGIN IMMEDIATE")
        with connection:
            _create_schema(connection)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")


def _create_schema(connection: sqlite3.Connection) -> None:
    if not _compatible(connection):
        for sql in _SCHEMA.values():
            connection.execute(sql)
        connection.execute("INSERT INTO store_meta VALUES (1, ?, 0)", (uuid4().hex,))
        connection.execute("PRAGMA user_version = 1")


def open_database(path: str | Path) -> sqlite3.Connection:
    """Open and validate the shared schema, initializing an empty database atomically, then enable WAL."""
    connection = sqlite3.connect(path, timeout=5.0, isolation_level=None, check_same_thread=False)
    try:
        _initialize(connection)
    except BaseException:
        connection.close()
        raise
    return connection


class SQLiteConnection:
    """One lazy connection, with synchronous use and close serialized by its private lock."""

    __slots__ = ("_closed", "_connection", "_lock", "_path")

    def __init__(self, path: str | Path) -> None:
        """Retain an explicit path without opening a database."""
        self._path = path
        self._connection: sqlite3.Connection | None = None
        self._lock = Lock()
        self._closed = False

    def run(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """Run database work under the lock, refusing use after close."""
        with self._lock:
            if self._closed:
                msg = "SQLite store is closed"
                raise ValueError(msg)
            if self._connection is None:
                self._connection = open_database(self._path)
            return operation(self._connection)

    def close(self) -> None:
        """Close once; closing an unused connection creates no file."""
        with self._lock:
            self._closed = True
            if self._connection is not None:
                connection, self._connection = self._connection, None
                connection.close()
