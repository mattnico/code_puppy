"""Conservative configuration for the opt-in native-agent runtime."""

from __future__ import annotations

import os
from pathlib import Path

from code_puppy.config import STATE_DIR, get_value, set_config_value

from .errors import NativeStorageUnavailableError

_ENABLED_KEY = "native_agents_enabled"
_DIAGNOSTICS_KEY = "native_agents_diagnostics_enabled"
_CODEACT_KEY = "native_agents_codeact_enabled"

_DEFAULTS = {
    "native_agents_store_retention_days": (30, 1, 3650),
    "native_agents_context_max_chars": (12_000, 256, 100_000),
    "native_agents_event_max_per_view": (30, 0, 500),
    "native_agents_state_max_bytes": (65_536, 1_048, 1_048_576),
}
_WARNED_INVALID_SETTINGS: set[str] = set()


def _warn_invalid_setting(name: str) -> None:
    if name in _WARNED_INVALID_SETTINGS:
        return
    _WARNED_INVALID_SETTINGS.add(name)
    try:
        from code_puppy.i18n import t
        from code_puppy.messaging import emit_warning

        emit_warning(t("native_agents.config.invalid_value", name=name))
    except Exception:
        # Configuration warnings are optional enrichment; a broken message bus
        # must never turn a safe fallback into a startup failure.
        return


def _truthy(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    """Return the master flag; missing, invalid, or unreadable values are off."""

    try:
        raw = get_value(_ENABLED_KEY)
    except Exception:
        return False
    return _truthy(raw)


def diagnostics_enabled() -> bool:
    try:
        raw = get_value(_DIAGNOSTICS_KEY)
    except Exception:
        return False
    return is_enabled() and _truthy(raw)


def codeact_enabled() -> bool:
    """Reserved flag; CodeAct remains unavailable in Tier A."""

    return False


def set_enabled(enabled: bool) -> None:
    set_config_value(_ENABLED_KEY, "true" if enabled else "false")


def database_path() -> Path:
    """Resolve native storage outside source/plugin directories."""

    override = os.environ.get("CODE_PUPPY_NATIVE_AGENTS_DB")
    if override:
        return Path(override).expanduser()
    return Path(STATE_DIR) / "native_agents" / "native_agents.sqlite3"


def bounded_int(name: str) -> int:
    """Read a bounded integer setting, falling back on invalid input."""

    default, minimum, maximum = _DEFAULTS[name]
    try:
        raw = get_value(name)
    except Exception:
        return default
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        _warn_invalid_setting(name)
        return default
    if not minimum <= value <= maximum:
        _warn_invalid_setting(name)
        return default
    return value


def store_retention_days() -> int:
    return bounded_int("native_agents_store_retention_days")


def context_max_chars() -> int:
    return bounded_int("native_agents_context_max_chars")


def event_max_per_view() -> int:
    return bounded_int("native_agents_event_max_per_view")


def state_max_bytes() -> int:
    return bounded_int("native_agents_state_max_bytes")


def validate_storage_path(path: Path) -> Path:
    """Reject an empty path before storage initialization."""

    if not str(path):
        raise NativeStorageUnavailableError("native storage path is empty")
    return path
