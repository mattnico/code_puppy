"""Execution-scoped in-memory references with opaque, expiring handles."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .contracts import (
    ExecutionIdentity,
    NativeEventKind,
    ReferenceHandle,
    ReferencePreview,
)
from .errors import HandleUnavailableError
from .events import EventStore, redact_payload

_DEFAULT_TTL = timedelta(minutes=30)


def handle_id_hash(handle_id: str) -> str:
    """Return a non-reversible correlation value for audit events."""

    return hashlib.sha256(handle_id.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class _Entry:
    handle: ReferenceHandle
    value: Any


class ReferenceStore:
    """Keep live values host-side; only handle metadata is model-visible."""

    def __init__(
        self,
        *,
        event_store: EventStore | None = None,
        default_ttl: timedelta = _DEFAULT_TTL,
    ) -> None:
        if default_ttl <= timedelta(0) or default_ttl > timedelta(hours=2):
            raise ValueError("reference TTL must be positive and bounded")
        self.event_store = event_store
        self.default_ttl = default_ttl
        self._entries: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        resource_type: str,
        value: Any,
        preview: ReferencePreview,
        execution: ExecutionIdentity,
        ttl: timedelta | None = None,
    ) -> ReferenceHandle:
        now = datetime.now(timezone.utc)
        expiry = now + (ttl or self.default_ttl)
        if expiry <= now or expiry - now > timedelta(hours=2):
            raise ValueError("reference TTL must be positive and bounded")
        safe_preview = ReferencePreview.model_validate(redact_payload(preview))
        handle = ReferenceHandle(
            handle_id=secrets.token_urlsafe(32),
            resource_type=resource_type,
            execution_id=execution.execution_id,
            owner_session_id=execution.session_id,
            created_at=now,
            expires_at=expiry,
            preview=safe_preview,
        )
        async with self._lock:
            expired = await self._purge_expired_locked(now)
            self._entries[handle.handle_id] = _Entry(handle=handle, value=value)
        for old_handle in expired:
            self._record_expired(old_handle)
        try:
            self._append_event(
                execution.execution_id,
                NativeEventKind.HANDLE_CREATED,
                {
                    "resource_type": resource_type,
                    "handle_id_hash": handle_id_hash(handle.handle_id),
                    "expires_at": handle.expires_at.isoformat(),
                    "preview_count": safe_preview.count,
                },
                strict=True,
            )
        except Exception:
            async with self._lock:
                self._entries.pop(handle.handle_id, None)
            raise
        return handle

    async def metadata(self, handle: ReferenceHandle | str) -> ReferenceHandle:
        """Return untrusted metadata without exposing the live value."""

        handle_id = self._coerce_handle_id(handle)
        now = datetime.now(timezone.utc)
        expired: ReferenceHandle | None = None
        async with self._lock:
            entry = self._entries.get(handle_id)
            if entry is None or entry.handle.expires_at <= now:
                if entry is not None:
                    expired = entry.handle
                    self._entries.pop(handle_id, None)
                else:
                    raise HandleUnavailableError("reference handle unavailable")
            else:
                return entry.handle
        self._record_expired(expired)
        raise HandleUnavailableError("reference handle unavailable")

    async def describe(
        self,
        handle: ReferenceHandle | str,
        *,
        execution: ExecutionIdentity,
        expected_type: str | None = None,
    ) -> ReferenceHandle:
        """Return metadata only after ownership/type/expiry checks."""

        metadata = await self.metadata(handle)
        if not self._is_owned(metadata, execution, expected_type):
            raise HandleUnavailableError("reference handle unavailable")
        return metadata

    async def resolve(
        self,
        handle: ReferenceHandle | str,
        *,
        execution: ExecutionIdentity,
        expected_type: str,
    ) -> Any:
        handle_id = self._coerce_handle_id(handle)
        now = datetime.now(timezone.utc)
        async with self._lock:
            entry = self._entries.get(handle_id)
            if entry is None or not self._is_owned(
                entry.handle, execution, expected_type
            ):
                raise HandleUnavailableError("reference handle unavailable")
            if entry.handle.expires_at <= now:
                self._entries.pop(handle_id, None)
                expired = entry.handle
            else:
                return entry.value
        self._record_expired(expired)
        raise HandleUnavailableError("reference handle unavailable")

    async def revoke_execution(self, execution_id: str) -> None:
        async with self._lock:
            doomed = [
                handle_id
                for handle_id, entry in self._entries.items()
                if entry.handle.execution_id == execution_id
            ]
            for handle_id in doomed:
                self._entries.pop(handle_id, None)
        for handle_id in doomed:
            self._append_event(
                execution_id,
                NativeEventKind.HANDLE_EXPIRED,
                {"handle_id_hash": handle_id_hash(handle_id), "reason": "revoked"},
            )

    async def purge_expired(self) -> int:
        async with self._lock:
            doomed = await self._purge_expired_locked(datetime.now(timezone.utc))
        for handle in doomed:
            self._record_expired(handle)
        return len(doomed)

    async def _purge_expired_locked(self, now: datetime) -> list[ReferenceHandle]:
        doomed = [
            entry.handle
            for entry in self._entries.values()
            if entry.handle.expires_at <= now
        ]
        for handle in doomed:
            self._entries.pop(handle.handle_id, None)
        return doomed

    def _record_expired(self, handle: ReferenceHandle | None) -> None:
        if handle is None:
            return
        self._append_event(
            handle.execution_id,
            NativeEventKind.HANDLE_EXPIRED,
            {
                "resource_type": handle.resource_type,
                "handle_id_hash": handle_id_hash(handle.handle_id),
            },
        )

    @staticmethod
    def _coerce_handle_id(handle: ReferenceHandle | str) -> str:
        if isinstance(handle, ReferenceHandle):
            return handle.handle_id
        if isinstance(handle, str) and handle:
            return handle
        raise HandleUnavailableError("reference handle unavailable")

    @staticmethod
    def _is_owned(
        handle: ReferenceHandle,
        execution: ExecutionIdentity,
        expected_type: str | None,
    ) -> bool:
        return (
            handle.execution_id == execution.execution_id
            and handle.owner_session_id == execution.session_id
            and (expected_type is None or handle.resource_type == expected_type)
        )

    def _append_event(
        self,
        execution_id: str,
        kind: NativeEventKind,
        payload: dict[str, Any],
        *,
        strict: bool = False,
    ) -> None:
        if self.event_store is None:
            return
        if strict:
            self.event_store.append(execution_id, kind, payload)
            return
        try:
            self.event_store.append(execution_id, kind, payload)
        except Exception:
            # Cleanup/audit enrichment is optional; never expose the live value.
            return
