"""Tests for :mod:`common.database`."""
from __future__ import annotations

from pathlib import Path

import pytest

from common.database import Database

# ---------------------------------------------------------------------------
# WAL mode
# ---------------------------------------------------------------------------


def test_journal_mode_is_wal(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    try:
        assert db.journal_mode == "wal"
    finally:
        db.close()


def test_creates_parent_directory(tmp_path: Path) -> None:
    """Connecting to a path under a missing parent must not fail."""
    nested = tmp_path / "deep" / "down" / "data.db"
    assert not nested.parent.exists()

    db = Database(nested)
    try:
        # Side effect: the file must have been created.
        assert nested.exists()
        assert db.journal_mode == "wal"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Round-trip: write then read
# ---------------------------------------------------------------------------


def test_insert_and_read_round_trip(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    try:
        db.execute_write(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )

        db.execute_write(
            "INSERT INTO items (name) VALUES (?)", ("apple",)
        )
        db.execute_write(
            "INSERT INTO items (name) VALUES (?)", ("banana",)
        )

        rows = db.execute("SELECT id, name FROM items ORDER BY id")
    finally:
        db.close()

    assert rows == [
        {"id": 1, "name": "apple"},
        {"id": 2, "name": "banana"},
    ]


def test_execute_returns_empty_list_when_no_rows(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    try:
        db.execute_write("CREATE TABLE empty (id INTEGER)")
        result = db.execute("SELECT id FROM empty")
    finally:
        db.close()

    assert result == []


def test_execute_returns_dicts_with_correct_types(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    try:
        db.execute_write(
            "CREATE TABLE kv (k TEXT PRIMARY KEY, v INTEGER NOT NULL)"
        )
        db.execute_write("INSERT INTO kv (k, v) VALUES (?, ?)", ("count", 42))

        rows = db.execute("SELECT k, v FROM kv WHERE k = ?", ("count",))
    finally:
        db.close()

    assert rows == [{"k": "count", "v": 42}]
    # sqlite3.Row yields native int for INTEGER columns.
    assert isinstance(rows[0]["v"], int)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_data_persists_across_close_and_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "persist.db"

    db = Database(db_path)
    try:
        db.execute_write("CREATE TABLE t (id INTEGER PRIMARY KEY, label TEXT)")
        db.execute_write("INSERT INTO t (label) VALUES (?)", ("hello",))
        db.execute_write("INSERT INTO t (label) VALUES (?)", ("world",))
    finally:
        db.close()

    # Re-open and verify data + WAL settings survive.
    db2 = Database(db_path)
    try:
        assert db2.journal_mode == "wal"
        rows = db2.execute("SELECT id, label FROM t ORDER BY id")
    finally:
        db2.close()

    assert rows == [
        {"id": 1, "label": "hello"},
        {"id": 2, "label": "world"},
    ]


def test_context_manager_closes_on_exit(tmp_path: Path) -> None:
    """Using ``Database`` as a context manager must close the connection."""
    db_path = tmp_path / "ctx.db"

    with Database(db_path) as db:
        db.execute_write("CREATE TABLE t (x INTEGER)")

    # After exiting the ``with`` block, attempting to use the connection
    # should fail because it has been closed.
    import sqlite3 as _sqlite3

    with pytest.raises(_sqlite3.ProgrammingError):
        db.execute_write("INSERT INTO t (x) VALUES (1)")
