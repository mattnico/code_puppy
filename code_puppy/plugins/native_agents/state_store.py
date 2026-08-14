"""Versioned native execution and Pydantic state persistence."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, TypeVar

from pydantic import BaseModel

from .contracts import (
    ExecutionIdentity,
    NativeExecutionRecord,
    NativeExecutionStatus,
    NativeStateEnvelope,
    NativeStrategyName,
)
from .errors import StateConflictError, StateSchemaError
from .contracts import NativeEventKind
from .events import append_event_on_connection, redact_payload
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

    def __init__(self, path: str, *, initialize: bool = True) -> None:
        self.path = path
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
                connection.execute("BEGIN")
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
    ) -> NativeExecutionRecord:
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
            connection.execute("BEGIN")
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
            connection.commit()
            row = connection.execute(
                "SELECT * FROM native_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        if row is None:  # pragma: no cover - guarded by rowcount
            raise StateSchemaError("native execution disappeared")
        return _record_from_row(row)

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
        encoded = json.dumps(
            state_json, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        envelope = NativeStateEnvelope(
            execution_id=execution_id,
            schema_name=schema_name,
            schema_version=schema_version,
            revision=1,
            state_json=state_json,
            created_at=now,
            updated_at=now,
        )
        with connect(self.path) as connection:
            connection.execute("BEGIN")
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
    ) -> NativeStateEnvelope:
        if expected_revision < 1 or schema_version < 1:
            raise StateSchemaError("state revision and schema version must be positive")
        state_json = _json_state(state)
        now = utc_now()
        encoded = json.dumps(
            state_json, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        with connect(self.path) as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT revision FROM native_state_snapshots WHERE execution_id = ? "
                "ORDER BY revision DESC LIMIT 1",
                (execution_id,),
            ).fetchone()
            current_revision = int(row[0]) if row else None
            if current_revision != expected_revision:
                raise StateConflictError(
                    f"expected state revision {expected_revision}, "
                    f"found {current_revision}"
                )
            revision = expected_revision + 1
            connection.execute(
                "INSERT INTO native_state_snapshots(execution_id, revision, schema_name, "
                "schema_version, state_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
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
                {"revision": revision, "schema_name": schema_name},
                occurred_at=now,
            )
            connection.commit()
        return NativeStateEnvelope(
            execution_id=execution_id,
            schema_name=schema_name,
            schema_version=schema_version,
            revision=revision,
            state_json=state_json,
            created_at=now,
            updated_at=now,
        )
