"""Lightweight SQLite helper with WAL mode enabled.

Uses the standard library's :mod:`sqlite3` only — no ORM — so it stays
predictable and side-effect free. Each :class:`Database` instance owns a
single connection and configures WAL plus ``synchronous=NORMAL`` so that
concurrent readers can coexist with writers.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class Database:
    """A single-connection SQLite wrapper with WAL enabled."""

    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._conn: sqlite3.Connection = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row

        # Enable WAL and NORMAL sync up-front. PRAGMA journal_mode is sticky
        # on the database file, so once set, every subsequent connection to
        # the same file inherits it (assuming the filesystem supports WAL).
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

    # ------------------------------------------------------------------ query

    def execute(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Run a read query and return rows as ``dict`` instances."""
        cursor = self._conn.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def execute_write(self, sql: str, params: tuple = ()) -> None:
        """Run a write statement (INSERT/UPDATE/DELETE/CREATE/...) and commit."""
        self._conn.execute(sql, params)
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    # ------------------------------------------------------------- properties

    @property
    def db_path(self) -> Path:
        """The on-disk path this database is bound to."""
        return self._db_path

    @property
    def journal_mode(self) -> str:
        """The active journal mode for this connection (expected: ``"wal"``)."""
        cursor = self._conn.execute("PRAGMA journal_mode")
        result = cursor.fetchone()
        return str(result[0]) if result is not None else ""

    # -------------------------------------------------------------- dunders

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
