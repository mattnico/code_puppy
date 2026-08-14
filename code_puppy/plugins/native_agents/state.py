"""Typed state service for bounded, revision-checked native mutations."""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .config import state_max_bytes
from .contracts import NativeStateEnvelope
from .errors import StateConflictError, StateSchemaError
from .events import EventStore
from .state_store import StateStore

StateModel = TypeVar("StateModel", bound=BaseModel)
_REASON_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


class StateService:
    """Own one execution's declared Pydantic state model."""

    def __init__(
        self,
        *,
        execution_id: str,
        state_type: type[StateModel],
        state_store: StateStore,
        event_store: EventStore,
        max_bytes: int | None = None,
    ) -> None:
        self.execution_id = execution_id
        self.state_type = state_type
        self.state_store = state_store
        self.event_store = event_store
        self.max_bytes = max_bytes or state_max_bytes()

    def _validate(self, state: StateModel) -> StateModel:
        if not isinstance(state, self.state_type):
            raise StateSchemaError("state does not match the declared state type")
        try:
            validated = self.state_type.model_validate(state.model_dump(mode="python"))
            encoded = json.dumps(
                validated.model_dump(mode="json"),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise StateSchemaError("state is not valid JSON for its schema") from exc
        if len(encoded.encode("utf-8")) > self.max_bytes:
            raise StateSchemaError("native state exceeds its serialized size limit")
        return validated

    @staticmethod
    def _reason(reason: str) -> str:
        if not isinstance(reason, str) or not _REASON_RE.fullmatch(reason):
            raise StateSchemaError("state mutation reason must be a stable identifier")
        return reason

    @staticmethod
    def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
        return sorted(
            key for key in set(before) | set(after) if before.get(key) != after.get(key)
        )

    def get(self) -> StateModel | None:
        snapshot = self.state_store.get_state(self.execution_id)
        if snapshot is None:
            return None
        try:
            return self.state_type.model_validate(snapshot.state_json)
        except ValidationError as exc:
            raise StateSchemaError("stored state no longer matches its schema") from exc

    def snapshot(self) -> NativeStateEnvelope | None:
        return self.state_store.get_state(self.execution_id)

    def initialize(self, state: StateModel) -> NativeStateEnvelope:
        validated = self._validate(state)
        snapshot = self.state_store.initialize_state(
            self.execution_id,
            validated,
            schema_name=self.state_type.__name__,
            schema_version=1,
        )
        return snapshot

    def replace(
        self,
        state: StateModel,
        *,
        expected_revision: int,
        reason: str,
    ) -> NativeStateEnvelope:
        validated = self._validate(state)
        reason = self._reason(reason)
        if expected_revision < 1:
            raise StateSchemaError("state revision must be positive")
        current = self.state_store.get_state(self.execution_id)
        if current is None:
            raise StateConflictError("native state has not been initialized")
        if current.revision != expected_revision:
            raise StateConflictError(
                f"expected state revision {expected_revision}, found {current.revision}"
            )
        changed_fields = self._changed_fields(
            current.state_json, validated.model_dump(mode="json")
        )
        return self.state_store.update_state(
            self.execution_id,
            expected_revision=expected_revision,
            state=validated,
            schema_name=self.state_type.__name__,
            schema_version=1,
            event_payload={
                "from_revision": expected_revision,
                "to_revision": expected_revision + 1,
                "changed_fields": changed_fields,
                "reason": reason,
            },
        )

    async def aget(self) -> StateModel | None:
        return self.get()

    async def ainitialize(self, state: StateModel) -> NativeStateEnvelope:
        return self.initialize(state)

    async def areplace(
        self,
        state: StateModel,
        *,
        expected_revision: int,
        reason: str,
    ) -> NativeStateEnvelope:
        return self.replace(state, expected_revision=expected_revision, reason=reason)
