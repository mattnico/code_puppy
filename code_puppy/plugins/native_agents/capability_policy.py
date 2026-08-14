"""Central, fail-closed policy checks for read-only capabilities."""

from __future__ import annotations

from .config import is_enabled
from .contracts import (
    AuthorizationDecision,
    CapabilityEffect,
    CapabilitySpec,
    ExecutionIdentity,
    MethodSpec,
    NativeStrategyName,
    ReferenceHandle,
)

_ALLOWED_PREDICT_EFFECTS = frozenset(
    {CapabilityEffect.OBSERVE, CapabilityEffect.COMPUTE, CapabilityEffect.PROPOSE}
)


class CapabilityPolicy:
    """Authorize declarations before handles are resolved or adapters run."""

    def authorize_declaration(
        self,
        *,
        method: MethodSpec,
        execution: ExecutionIdentity,
        capability: CapabilitySpec,
    ) -> AuthorizationDecision:
        """Check policy independent of live-handle metadata."""

        if not is_enabled():
            return AuthorizationDecision(
                allowed=False, reason="Capability is not permitted for this method."
            )
        if method.name != execution.method_name:
            return AuthorizationDecision(
                allowed=False, reason="Capability is not permitted for this execution."
            )
        if method.strategy is not NativeStrategyName.PREDICT:
            return AuthorizationDecision(
                allowed=False, reason="Capability strategy is not permitted."
            )
        if capability.name not in method.allowed_capabilities:
            return AuthorizationDecision(
                allowed=False, reason="Capability is not permitted for this method."
            )
        if capability.effect not in _ALLOWED_PREDICT_EFFECTS:
            return AuthorizationDecision(
                allowed=False, reason="Capability effect is not permitted."
            )
        return AuthorizationDecision(allowed=True, reason="allowed")

    def authorize(
        self,
        *,
        method: MethodSpec,
        execution: ExecutionIdentity,
        handle: ReferenceHandle,
        capability: CapabilitySpec,
    ) -> AuthorizationDecision:
        decision = self.authorize_declaration(
            method=method,
            execution=execution,
            capability=capability,
        )
        if not decision.allowed:
            return decision
        if capability.resource_type != handle.resource_type:
            return AuthorizationDecision(
                allowed=False, reason="Capability resource type is not permitted."
            )
        if handle.execution_id != execution.execution_id:
            return AuthorizationDecision(
                allowed=False, reason="Capability is not permitted for this execution."
            )
        if handle.owner_session_id != execution.session_id:
            return AuthorizationDecision(
                allowed=False, reason="Capability is not permitted for this session."
            )
        return decision
