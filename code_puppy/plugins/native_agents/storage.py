"""SQLite connection and migration helpers for native-agent records."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .errors import NativeStorageUnavailableError

LATEST_SCHEMA_VERSION = 2

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE native_executions (
          execution_id TEXT PRIMARY KEY,
          agent_name TEXT NOT NULL,
          method_name TEXT NOT NULL,
          method_version INTEGER NOT NULL,
          strategy TEXT NOT NULL,
          session_id TEXT,
          parent_execution_id TEXT,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL,
          started_at TEXT,
          finished_at TEXT,
          error_code TEXT,
          error_summary TEXT
        );

        CREATE TABLE native_state_snapshots (
          execution_id TEXT NOT NULL,
          revision INTEGER NOT NULL,
          schema_name TEXT NOT NULL,
          schema_version INTEGER NOT NULL,
          state_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY (execution_id, revision),
          FOREIGN KEY (execution_id) REFERENCES native_executions(execution_id)
        );

        CREATE TABLE native_events (
          event_id TEXT PRIMARY KEY,
          execution_id TEXT NOT NULL,
          sequence INTEGER NOT NULL,
          kind TEXT NOT NULL,
          occurred_at TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          redacted INTEGER NOT NULL DEFAULT 0,
          UNIQUE (execution_id, sequence),
          FOREIGN KEY (execution_id) REFERENCES native_executions(execution_id)
        );

        CREATE INDEX idx_native_events_execution_sequence
          ON native_events(execution_id, sequence);
        CREATE INDEX idx_native_executions_session_created
          ON native_executions(session_id, created_at DESC);
        """,
    ),
    (
        2,
        "ALTER TABLE native_events ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1;",
    ),
)

_REQUIRED_COLUMNS = {
    "native_executions": {
        "execution_id",
        "agent_name",
        "method_name",
        "method_version",
        "strategy",
        "status",
        "created_at",
    },
    "native_state_snapshots": {
        "execution_id",
        "revision",
        "schema_name",
        "schema_version",
        "state_json",
        "created_at",
    },
    "native_events": {
        "event_id",
        "execution_id",
        "sequence",
        "schema_version",
        "kind",
        "occurred_at",
        "payload_json",
        "redacted",
    },
}


def _verify_schema(connection: sqlite3.Connection) -> None:
    """Fail closed when a database is incomplete, future, or corrupt."""

    applied = [
        int(row[0])
        for row in connection.execute(
            "SELECT version FROM native_schema_migrations ORDER BY version"
        ).fetchall()
    ]
    expected = list(range(1, LATEST_SCHEMA_VERSION + 1))
    if applied != expected:
        raise NativeStorageUnavailableError("native storage schema is unsupported")
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise NativeStorageUnavailableError("native storage integrity check failed")
    for table, required_columns in _REQUIRED_COLUMNS.items():
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        columns = {row[1] for row in rows}
        if not required_columns.issubset(columns):
            raise NativeStorageUnavailableError("native storage schema is incomplete")


def utc_now() -> datetime:
    """Return an aware UTC timestamp for persisted records."""

    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    """Serialize a timestamp without accepting naive values."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("native timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp and require timezone information."""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored native timestamps must be timezone-aware")
    return parsed


@contextmanager
def connect(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open one short-lived connection with foreign keys enabled."""

    db_path = Path(path)
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            db_path,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
    except (OSError, sqlite3.Error) as exc:
        raise NativeStorageUnavailableError("native storage is unavailable") from exc

    try:
        yield connection
    finally:
        connection.close()


def initialize_database(path: str | Path) -> None:
    """Apply all migrations atomically and idempotently."""

    try:
        with connect(path) as connection:
            connection.execute("BEGIN")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS native_schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row[0]
                for row in connection.execute(
                    "SELECT version FROM native_schema_migrations"
                ).fetchall()
            }
            for version, sql in _MIGRATIONS:
                if version in applied:
                    continue
                for statement in (part.strip() for part in sql.split(";")):
                    if statement:
                        connection.execute(statement)
                connection.execute(
                    "INSERT INTO native_schema_migrations(version, applied_at) "
                    "VALUES (?, ?)",
                    (version, timestamp(utc_now())),
                )
            connection.commit()
            _verify_schema(connection)
    except NativeStorageUnavailableError:
        raise
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise NativeStorageUnavailableError("native storage migration failed") from exc


def schema_version(path: str | Path) -> int:
    """Return the highest applied migration, or zero for an empty database."""

    try:
        with connect(path) as connection:
            row = connection.execute(
                "SELECT MAX(version) FROM native_schema_migrations"
            ).fetchone()
            return int(row[0] or 0)
    except NativeStorageUnavailableError:
        raise
    except sqlite3.Error as exc:
        raise NativeStorageUnavailableError(
            "native storage schema is unreadable"
        ) from exc
