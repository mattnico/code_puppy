"""Versioned native execution and Pydantic state persistence."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from pydantic import BaseModel

from .contracts import (
    ExecutionIdentity,
    NativeExecutionRecord,
    NativeExecutionStatus,
    NativeStateEnvelope,
    NativeStrategyName,
)
from .errors import (
    NativeStorageUnavailableError,
    StateConflictError,
    StateSchemaError,
)
from .contracts import NativeEventKind
from .config import state_max_bytes
from .events import append_event_on_connection, redact_payload
from .lifecycle import validate_transition
from .storage import connect, initialize_database, parse_timestamp, timestamp, utc_now

StateModel = TypeVar("StateModel", bound=BaseModel)


def _json_state(state: BaseModel) -> dict[str, Any]:
    if not isinstance(state, BaseModel):
        raise StateSchemaError("native state must be a Pydantic model")
    try:
        return redact_payload(state)
    except Exception as exc:
        if isinstance(exc, StateSchemaError):
            raise
        raise StateSchemaError("native state must be JSON-compatible") from exc


def _encode_state(state_json: dict[str, Any], max_bytes: int) -> str:
    try:
        encoded = json.dumps(
            state_json, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise StateSchemaError("native state must be JSON-compatible") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise StateSchemaError("native state exceeds its serialized size limit")
    return encoded


def _record_from_row(row: sqlite3.Row) -> NativeExecutionRecord:
    try:
        return NativeExecutionRecord(
            execution_id=row["execution_id"],
            agent_name=row["agent_name"],
            method_name=row["method_name"],
            method_version=int(row["method_version"]),
            strategy=NativeStrategyName(row["strategy"]),
            session_id=row["session_id"],
            parent_execution_id=row["parent_execution_id"],
            status=NativeExecutionStatus(row["status"]),
            created_at=parse_timestamp(row["created_at"]),
            started_at=(
                parse_timestamp(row["started_at"]) if row["started_at"] else None
            ),
            finished_at=(
                parse_timestamp(row["finished_at"]) if row["finished_at"] else None
            ),
            error_code=row["error_code"],
            error_summary=row["error_summary"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StateSchemaError("stored native execution is invalid") from exc


def _state_from_row(row: sqlite3.Row) -> NativeStateEnvelope:
    try:
        return NativeStateEnvelope(
            execution_id=row["execution_id"],
            schema_name=row["schema_name"],
            schema_version=int(row["schema_version"]),
            revision=int(row["revision"]),
            state_json=json.loads(row["state_json"]),
            created_at=parse_timestamp(row["created_at"]),
            updated_at=parse_timestamp(row["updated_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StateSchemaError("stored native state is invalid") from exc


class StateStore:
    """SQLite-backed execution records and optimistic state snapshots."""

    def __init__(
        self,
        path: str,
        *,
        initialize: bool = True,
        max_state_bytes: int | None = None,
    ) -> None:
        self.path = path
        self.max_state_bytes = (
            state_max_bytes() if max_state_bytes is None else max_state_bytes
        )
        if self.max_state_bytes < 1:
            raise StateSchemaError("native state size limit must be positive")
        if initialize:
            initialize_database(path)

    def create_execution(
        self,
        identity: ExecutionIdentity,
        *,
        method_version: int,
        strategy: NativeStrategyName,
    ) -> NativeExecutionRecord:
        now = identity.created_at
        record = NativeExecutionRecord(
            execution_id=identity.execution_id,
            agent_name=identity.agent_name,
            method_name=identity.method_name,
            method_version=method_version,
            strategy=strategy,
            session_id=identity.session_id,
            parent_execution_id=identity.parent_execution_id,
            status=NativeExecutionStatus.CREATED,
            created_at=now,
        )
        try:
            with connect(self.path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO native_executions(execution_id, agent_name, "
                    "method_name, method_version, strategy, session_id, "
                    "parent_execution_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.execution_id,
                        record.agent_name,
                        record.method_name,
                        record.method_version,
                        record.strategy.value,
                        record.session_id,
                        record.parent_execution_id,
                        record.status.value,
                        timestamp(record.created_at),
                    ),
                )
                connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            raise StateSchemaError("native execution already exists") from exc
        except sqlite3.Error as exc:
            raise NativeStorageUnavailableError(
                "native execution write failed"
            ) from exc

    def get_execution(self, execution_id: str) -> NativeExecutionRecord | None:
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM native_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        return _record_from_row(row) if row else None

    def set_execution_status(
        self,
        execution_id: str,
        status: NativeExecutionStatus,
        *,
        error_code: str | None = None,
        error_summary: str | None = None,
        events: tuple[tuple[NativeEventKind, dict[str, Any]], ...] = (),
    ) -> NativeExecutionRecord:
        if not isinstance(status, NativeExecutionStatus):
            raise StateSchemaError("native execution status is invalid")
        if error_code is not None and (
            not isinstance(error_code, str)
            or not error_code
            or len(error_code) > 200
            or any(character.isspace() for character in error_code)
        ):
            raise StateSchemaError("native execution error code is invalid")
        if error_summary is not None:
            try:
                error_summary = redact_payload(
                    {"summary": str(error_summary).replace("\n", " ")[:2_000]}
                )["summary"]
            except Exception as exc:
                raise StateSchemaError(
                    "native execution error summary is invalid"
                ) from exc
        now = utc_now()
        started_at = timestamp(now) if status is NativeExecutionStatus.RUNNING else None
        finished_at = (
            timestamp(now)
            if status
            in {
                NativeExecutionStatus.FINISHED,
                NativeExecutionStatus.FAILED,
                NativeExecutionStatus.CANCELLED,
            }
            else None
        )
        with connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current_row = connection.execute(
                "SELECT status, started_at, finished_at FROM native_executions "
                "WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if current_row is None:
                raise StateSchemaError("native execution does not exist")
            try:
                current_status = NativeExecutionStatus(current_row[0])
                validate_transition(current_status, status)
            except (ValueError, TypeError) as exc:
                raise StateSchemaError(
                    "stored native execution status is invalid"
                ) from exc
            if current_status is NativeExecutionStatus.RUNNING:
                started_at = current_row[1]
            if current_status in {
                NativeExecutionStatus.FINISHED,
                NativeExecutionStatus.FAILED,
                NativeExecutionStatus.CANCELLED,
            }:
                finished_at = current_row[2]
            result = connection.execute(
                "UPDATE native_executions SET status = ?, started_at = COALESCE(?, started_at), "
                "finished_at = COALESCE(?, finished_at), error_code = ?, error_summary = ? "
                "WHERE execution_id = ?",
                (
                    status.value,
                    started_at,
                    finished_at,
                    error_code,
                    error_summary,
                    execution_id,
                ),
            )
            if result.rowcount != 1:
                raise StateSchemaError("native execution does not exist")
            if events:
                for event_kind, event_payload in events:
                    append_event_on_connection(
                        connection,
                        execution_id,
                        event_kind,
                        event_payload,
                        occurred_at=now,
                    )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM native_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        if row is None:  # pragma: no cover - guarded by rowcount
            raise StateSchemaError("native execution disappeared")
        return _record_from_row(row)

    def purge_expired(self, retention_days: int, *, now: datetime | None = None) -> int:
        """Delete only terminal records older than the bounded retention window."""

        if not 1 <= retention_days <= 3_650:
            raise StateSchemaError("retention days are outside the safe bound")
        current = now or utc_now()
        if current.tzinfo is None or current.utcoffset() is None:
            raise StateSchemaError("cleanup time must be timezone-aware")
        cutoff = current.astimezone(timezone.utc) - timedelta(days=retention_days)
        terminal = tuple(
            status.value
            for status in (
                NativeExecutionStatus.FINISHED,
                NativeExecutionStatus.FAILED,
                NativeExecutionStatus.CANCELLED,
            )
        )
        placeholders = ",".join("?" for _ in terminal)
        with connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT execution_id FROM native_executions "
                f"WHERE status IN ({placeholders}) AND created_at < ?",
                (*terminal, timestamp(cutoff)),
            ).fetchall()
            execution_ids = [row[0] for row in rows]
            for execution_id in execution_ids:
                connection.execute(
                    "DELETE FROM native_events WHERE execution_id = ?", (execution_id,)
                )
                connection.execute(
                    "DELETE FROM native_state_snapshots WHERE execution_id = ?",
                    (execution_id,),
                )
                connection.execute(
                    "DELETE FROM native_executions WHERE execution_id = ?",
                    (execution_id,),
                )
            connection.commit()
        return len(execution_ids)

    def initialize_state(
        self,
        execution_id: str,
        state: StateModel,
        *,
        schema_name: str,
        schema_version: int,
    ) -> NativeStateEnvelope:
        if schema_version < 1:
            raise StateSchemaError("state schema version must be positive")
        state_json = _json_state(state)
        now = utc_now()
        encoded = _encode_state(state_json, self.max_state_bytes)
        envelope = NativeStateEnvelope(
            execution_id=execution_id,
            schema_name=schema_name,
            schema_version=schema_version,
            revision=1,
            state_json=state_json,
            created_at=now,
            updated_at=now,
        )
        try:
            with connect(self.path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO native_state_snapshots(execution_id, revision, schema_name, "
                    "schema_version, state_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        execution_id,
                        1,
                        schema_name,
                        schema_version,
                        encoded,
                        timestamp(now),
                    ),
                )
                append_event_on_connection(
                    connection,
                    execution_id,
                    NativeEventKind.STATE_INITIALIZED,
                    {"revision": 1, "schema_name": schema_name},
                    occurred_at=now,
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise StateSchemaError("native state initialization failed") from exc
        except sqlite3.Error as exc:
            raise NativeStorageUnavailableError("native state write failed") from exc
        return envelope

    def get_state(self, execution_id: str) -> NativeStateEnvelope | None:
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT latest.execution_id, latest.revision, latest.schema_name, "
                "latest.schema_version, latest.state_json, initial.created_at, "
                "latest.created_at AS updated_at "
                "FROM native_state_snapshots latest "
                "JOIN native_state_snapshots initial "
                "ON initial.execution_id = latest.execution_id AND initial.revision = 1 "
                "WHERE latest.execution_id = ? "
                "ORDER BY latest.revision DESC LIMIT 1",
                (execution_id,),
            ).fetchone()
        return _state_from_row(row) if row else None

    def update_state(
        self,
        execution_id: str,
        *,
        expected_revision: int,
        state: StateModel,
        schema_name: str,
        schema_version: int,
        event_payload: dict[str, Any] | None = None,
    ) -> NativeStateEnvelope:
        if expected_revision < 1 or schema_version < 1:
            raise StateSchemaError("state revision and schema version must be positive")
        state_json = _json_state(state)
        now = utc_now()
        encoded = _encode_state(state_json, self.max_state_bytes)
        try:
            with connect(self.path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT revision FROM native_state_snapshots "
                    "WHERE execution_id = ? ORDER BY revision DESC LIMIT 1",
                    (execution_id,),
                ).fetchone()
                current_revision = int(row[0]) if row else None
                if current_revision != expected_revision:
                    raise StateConflictError(
                        f"expected state revision {expected_revision}, "
                        f"found {current_revision}"
                    )
                revision = expected_revision + 1
                initial_row = connection.execute(
                    "SELECT created_at FROM native_state_snapshots "
                    "WHERE execution_id = ? AND revision = 1",
                    (execution_id,),
                ).fetchone()
                if initial_row is None:
                    raise StateSchemaError(
                        "native state is missing its initial snapshot"
                    )
                created_at = parse_timestamp(initial_row[0])
                connection.execute(
                    "INSERT INTO native_state_snapshots(execution_id, revision, "
                    "schema_name, schema_version, state_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        execution_id,
                        revision,
                        schema_name,
                        schema_version,
                        encoded,
                        timestamp(now),
                    ),
                )
                append_event_on_connection(
                    connection,
                    execution_id,
                    NativeEventKind.STATE_UPDATED,
                    event_payload or {"revision": revision, "schema_name": schema_name},
                    occurred_at=now,
                )
                connection.commit()
        except (StateConflictError, StateSchemaError):
            raise
        except sqlite3.IntegrityError as exc:
            raise StateSchemaError("native state update failed") from exc
        except sqlite3.Error as exc:
            raise NativeStorageUnavailableError("native state write failed") from exc
        return NativeStateEnvelope(
            execution_id=execution_id,
            schema_name=schema_name,
            schema_version=schema_version,
            revision=revision,
            state_json=state_json,
            created_at=created_at,
            updated_at=now,
        )
