"""Callback registration for the opt-in native-agent plugin."""

from __future__ import annotations

import logging
from threading import Lock

from code_puppy.callbacks import register_callback
from code_puppy.messaging import bus_emit_debug

from .config import database_path, is_enabled
from .commands import handle_native_command, native_command_help
from .registry import registered_agents
from .storage import initialize_database

logger = logging.getLogger(__name__)

_STORAGE_READY = False
_STORAGE_LOCK = Lock()


def storage_ready() -> bool:
    """Return whether the most recent enabled startup initialized storage."""

    return _STORAGE_READY


def _initialize_if_enabled() -> None:
    """Initialize native storage only after the master flag opts in."""

    global _STORAGE_READY
    if not is_enabled():
        return
    with _STORAGE_LOCK:
        if _STORAGE_READY:
            return
        try:
            initialize_database(database_path())
        except Exception as exc:  # optional plugin must fail soft
            _STORAGE_READY = False
            logger.warning("native-agent storage disabled: %s", exc)
            bus_emit_debug(
                "native-agent storage initialization failed; feature disabled"
            )
            return
        _STORAGE_READY = True


def _feature_capability(name: str) -> bool | None:
    if name == "native_agents":
        return is_enabled() and storage_ready()
    return None


register_callback("startup", _initialize_if_enabled)
register_callback("custom_command", handle_native_command)
register_callback("custom_command_help", native_command_help)
register_callback("register_agents", registered_agents)
register_callback("feature_capability", _feature_capability)
