"""Immutable native events, bounded queries, and defense-in-depth redaction."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from .contracts import JsonValue, NativeEvent, NativeEventKind
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
    r"private[_-]?key|secret|session[_-]?cookie|refresh[_-]?token)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:bearer\s+|basic\s+)[a-z0-9._~+/=-]{8,}|"
    r"(?:https?|postgres(?:ql)?|mysql)://[^\s]+:[^\s]+@[^\s]+"
)
_REDACTED = "[REDACTED]"


def _is_secret_field(field_info: Any) -> bool:
    extra = getattr(field_info, "json_schema_extra", None)
    return isinstance(extra, Mapping) and extra.get("secret") is True


def _redact_value(value: Any) -> Any:
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
    """Return a strict JSON-compatible mapping with secrets redacted."""

    if isinstance(value, BaseModel):
        raw = value.model_dump(mode="json")
        for name, field_info in value.__class__.model_fields.items():
            if _is_secret_field(field_info) and name in raw:
                raw[name] = _REDACTED
        value = raw
    if not isinstance(value, Mapping):
        raise EventStoreError("native payload must be a mapping")
    redacted = _redact_value(value)
    try:
        encoded = json.dumps(redacted, ensure_ascii=False, allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise EventStoreError("native payload is not JSON-compatible") from exc
    if not isinstance(decoded, dict):  # pragma: no cover - guarded above
        raise EventStoreError("native payload must be a JSON object")
    return decoded


def _event_from_row(row: sqlite3.Row) -> NativeEvent:
    try:
        payload = json.loads(row["payload_json"])
        return NativeEvent(
            event_id=row["event_id"],
            execution_id=row["execution_id"],
            sequence=int(row["sequence"]),
            kind=NativeEventKind(row["kind"]),
            occurred_at=parse_timestamp(row["occurred_at"]),
            payload=payload,
            redacted=bool(row["redacted"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EventStoreError("stored native event is invalid") from exc


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
        kind=kind,
        occurred_at=occurred_at or utc_now(),
        payload=redacted_payload,
        redacted=redacted_payload != dict(payload)
        if isinstance(payload, Mapping)
        else True,
    )
    connection.execute(
        "INSERT INTO native_events(event_id, execution_id, sequence, kind, "
        "occurred_at, payload_json, redacted) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event.event_id,
            event.execution_id,
            event.sequence,
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
                connection.execute("BEGIN")
                event = append_event_on_connection(
                    connection, execution_id, kind, payload
                )
                connection.commit()
                return event
        except EventStoreError:
            raise
        except sqlite3.Error as exc:
            raise EventStoreError("native event append failed") from exc

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
        try:
            with connect(self.path) as connection:
                rows = connection.execute(
                    "SELECT event_id, execution_id, sequence, kind, occurred_at, "
                    "payload_json, redacted FROM native_events "
                    "WHERE execution_id = ? AND sequence > ? "
                    "ORDER BY sequence ASC LIMIT ?",
                    (execution_id, after_sequence, limit),
                ).fetchall()
            return [_event_from_row(row) for row in rows]
        except EventStoreError:
            raise
        except sqlite3.Error as exc:
            raise EventStoreError("native event query failed") from exc
