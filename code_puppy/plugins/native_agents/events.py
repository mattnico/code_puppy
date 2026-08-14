"""Immutable native events, bounded queries, and defense-in-depth redaction."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from .contracts import (
    EventQuery,
    EventSummary,
    JsonValue,
    NativeEvent,
    NativeEventKind,
)
from .errors import EventStoreError
from .storage import (
    connect,
    initialize_database,
    parse_timestamp,
    timestamp,
    utc_now,
)

_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|auth(?:orization)?|credential|password|"
    r"private[_-]?key|client[_-]?secret|token|secret|session[_-]?cookie|"
    r"refresh[_-]?token)",
    re.IGNORECASE,
)

_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:bearer\s+|basic\s+)[a-z0-9._~+/=-]{8,}|"
    r"(?:https?|postgres(?:ql)?|mysql)://[^\s]+:[^\s]+@[^\s]+|"
    r"(?:api[_-]?key|access[_-]?token|authorization|password|secret)"
    r"\s*[:=]\s*[^\s,;]+"
)
_REDACTED = "[REDACTED]"
_MAX_EVENT_PAYLOAD_BYTES = 65_536


def _is_secret_field(field_info: Any) -> bool:
    extra = getattr(field_info, "json_schema_extra", None)
    return isinstance(extra, Mapping) and extra.get("secret") is True


def _redact_model_mapping(serialized: Any, original: BaseModel) -> Any:
    """Apply field metadata while recursively redacting nested models."""

    if not isinstance(serialized, Mapping):
        return _redact_value(serialized)
    fields = getattr(type(original), "model_fields", {})
    result: dict[str, Any] = {}
    for key, item in serialized.items():
        name = str(key)
        field_info = fields.get(name)
        if field_info is not None and (
            _is_secret_field(field_info) or _SECRET_KEY_RE.search(name)
        ):
            result[name] = _REDACTED
            continue
        original_item = getattr(original, name, None)
        result[name] = _redact_with_original(item, original_item)
    return result


def _redact_with_original(serialized: Any, original: Any) -> Any:
    if isinstance(original, BaseModel):
        return _redact_model_mapping(serialized, original)
    if (
        isinstance(original, Sequence)
        and not isinstance(original, (str, bytes, bytearray))
        and isinstance(serialized, Sequence)
        and not isinstance(serialized, (str, bytes, bytearray))
    ):
        return [
            _redact_with_original(item, source)
            for item, source in zip(serialized, original)
        ]
    return _redact_value(serialized)


def _redact_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        try:
            return _redact_model_mapping(value.model_dump(mode="json"), value)
        except Exception as exc:
            raise EventStoreError("native payload is not JSON-compatible") from exc
    if isinstance(value, Mapping):
        return {
            str(key): (
                _REDACTED if _SECRET_KEY_RE.search(str(key)) else _redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_value(item) for item in value]
    if isinstance(value, str) and _SECRET_VALUE_RE.search(value):
        return _REDACTED
    return value


def redact_payload(value: Any) -> dict[str, JsonValue]:
    """Return a strict JSON-compatible mapping with nested secrets redacted."""

    try:
        if isinstance(value, BaseModel):
            serialized = value.model_dump(mode="json")
            redacted = _redact_model_mapping(serialized, value)
        elif isinstance(value, Mapping):
            redacted = _redact_value(value)
        else:
            raise EventStoreError("native payload must be a mapping")
        encoded = json.dumps(redacted, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > _MAX_EVENT_PAYLOAD_BYTES:
            raise EventStoreError("native event payload exceeds its size limit")
        decoded = json.loads(encoded)
    except EventStoreError:
        raise
    except (TypeError, ValueError, ValidationError) as exc:
        raise EventStoreError("native payload is not JSON-compatible") from exc
    if not isinstance(decoded, dict):  # pragma: no cover - guarded above
        raise EventStoreError("native payload must be a JSON object")
    return decoded


def _event_from_row(row: sqlite3.Row, *, include_payload: bool = True) -> NativeEvent:
    try:
        raw_payload = json.loads(row["payload_json"])
        safe_payload = redact_payload(raw_payload)
        return NativeEvent(
            event_id=row["event_id"],
            execution_id=row["execution_id"],
            sequence=int(row["sequence"]),
            schema_version=int(row["schema_version"]),
            kind=NativeEventKind(row["kind"]),
            occurred_at=parse_timestamp(row["occurred_at"]),
            payload=safe_payload if include_payload else {},
            redacted=bool(row["redacted"]) or safe_payload != raw_payload,
        )
    except (KeyError, TypeError, ValueError, ValidationError, EventStoreError) as exc:
        raise EventStoreError("stored native event is invalid") from exc


def _summary_text(event: NativeEvent) -> str:
    """Produce a deterministic bounded summary without raw payload dumps."""

    summary = event.kind.value.replace("_", " ")
    if event.kind is NativeEventKind.VALIDATION_FAILED:
        code = event.payload.get("error_code")
        if isinstance(code, str):
            summary += f": {code}"
    elif event.kind in {
        NativeEventKind.STATE_INITIALIZED,
        NativeEventKind.STATE_UPDATED,
    }:
        revision = event.payload.get("revision") or event.payload.get("to_revision")
        if isinstance(revision, int):
            summary += f" revision {revision}"
    return summary[:500]


def append_event_on_connection(
    connection: sqlite3.Connection,
    execution_id: str,
    kind: NativeEventKind,
    payload: Mapping[str, Any] | BaseModel,
    *,
    occurred_at: datetime | None = None,
) -> NativeEvent:
    """Append one event using the caller's active transaction."""

    redacted_payload = redact_payload(payload)
    if isinstance(payload, BaseModel):
        original_payload = payload.model_dump(mode="json")
    else:
        original_payload = dict(payload)
    encoded = json.dumps(
        redacted_payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )

    row = connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM native_events "
        "WHERE execution_id = ?",
        (execution_id,),
    ).fetchone()
    sequence = int(row[0])
    event = NativeEvent(
        event_id=str(uuid.uuid4()),
        execution_id=execution_id,
        sequence=sequence,
        schema_version=1,
        kind=kind,
        occurred_at=occurred_at or utc_now(),
        payload=redacted_payload,
        redacted=redacted_payload != original_payload,
    )
    connection.execute(
        "INSERT INTO native_events(event_id, execution_id, sequence, schema_version, kind, "
        "occurred_at, payload_json, redacted) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event.event_id,
            event.execution_id,
            event.sequence,
            event.schema_version,
            event.kind.value,
            timestamp(event.occurred_at),
            encoded,
            int(event.redacted),
        ),
    )
    return event


class EventStore:
    """Short-lived-connection event store with append-only semantics."""

    def __init__(self, path: str) -> None:
        self.path = path
        initialize_database(path)

    def append(
        self,
        execution_id: str,
        kind: NativeEventKind,
        payload: Mapping[str, Any] | BaseModel,
    ) -> NativeEvent:
        try:
            with connect(self.path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                event = append_event_on_connection(
                    connection, execution_id, kind, payload
                )
                connection.commit()
                return event
        except EventStoreError:
            raise
        except sqlite3.Error as exc:
            raise EventStoreError("native event append failed") from exc

    def query(self, execution_id: str, query: EventQuery) -> list[NativeEvent]:
        """Query one execution with an explicit hard limit."""

        try:
            with connect(self.path) as connection:
                sql = (
                    "SELECT event_id, execution_id, sequence, schema_version, kind, occurred_at, "
                    "payload_json, redacted FROM native_events "
                    "WHERE execution_id = ?"
                )
                params: list[Any] = [execution_id]
                if query.kinds:
                    placeholders = ",".join("?" for _ in query.kinds)
                    sql += f" AND kind IN ({placeholders})"
                    params.extend(kind.value for kind in query.kinds)
                if query.after_sequence is not None:
                    sql += " AND sequence > ?"
                    params.append(query.after_sequence)
                sql += " ORDER BY sequence ASC LIMIT ?"
                params.append(query.limit)
                rows = connection.execute(sql, params).fetchall()
            return [
                _event_from_row(row, include_payload=query.include_payload)
                for row in rows
            ]
        except sqlite3.Error as exc:
            raise EventStoreError("native event query failed") from exc

    def list_events(
        self,
        execution_id: str,
        *,
        limit: int,
        after_sequence: int = 0,
    ) -> list[NativeEvent]:
        if not 1 <= limit <= 500:
            raise EventStoreError("event query limit must be between 1 and 500")
        if after_sequence < 0:
            raise EventStoreError("event sequence must not be negative")
        return self.query(
            execution_id,
            EventQuery(
                limit=limit, after_sequence=after_sequence, include_payload=True
            ),
        )

    def recent_summaries(
        self, execution_id: str, query: EventQuery
    ) -> list[EventSummary]:
        """Return the newest bounded summaries in display order."""

        summary_query = query.model_copy(update={"include_payload": True})
        try:
            with connect(self.path) as connection:
                sql = (
                    "SELECT event_id, execution_id, sequence, schema_version, kind, occurred_at, "
                    "payload_json, redacted FROM native_events "
                    "WHERE execution_id = ?"
                )
                params: list[Any] = [execution_id]
                if summary_query.kinds:
                    placeholders = ",".join("?" for _ in summary_query.kinds)
                    sql += f" AND kind IN ({placeholders})"
                    params.extend(kind.value for kind in summary_query.kinds)
                if summary_query.after_sequence is not None:
                    sql += " AND sequence > ?"
                    params.append(summary_query.after_sequence)
                sql += " ORDER BY sequence DESC LIMIT ?"
                params.append(summary_query.limit)
                rows = connection.execute(sql, params).fetchall()
            events = [_event_from_row(row) for row in reversed(rows)]
            return [
                EventSummary(
                    sequence=event.sequence,
                    kind=event.kind,
                    occurred_at=event.occurred_at,
                    summary=_summary_text(event),
                )
                for event in events
            ]
        except sqlite3.Error as exc:
            raise EventStoreError("native event query failed") from exc

    def summaries(self, execution_id: str, query: EventQuery) -> list[EventSummary]:
        """Return deterministic bounded summaries with payloads omitted."""

        return self.recent_summaries(execution_id, query)


class EventService:
    """Execution-owned query facade; cross-execution reads are impossible."""

    __slots__ = ("_execution_id", "store")

    def __init__(self, execution_id: str, store: EventStore) -> None:
        self._execution_id = execution_id
        self.store = store

    @property
    def execution_id(self) -> str:
        return self._execution_id

    def query(self, query: EventQuery) -> list[NativeEvent]:
        return self.store.query(self._execution_id, query)

    def summaries(self, query: EventQuery) -> list[EventSummary]:
        return self.store.summaries(self._execution_id, query)
