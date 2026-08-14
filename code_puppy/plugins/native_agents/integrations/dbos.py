"""Optional DBOS integration with safe absent/not-launched behavior."""

from __future__ import annotations

from typing import Any


def status() -> dict[str, Any]:
    """Report DBOS availability without importing or launching it."""

    try:
        from code_puppy.plugins.dbos_durable_exec.lifecycle import is_launched
        from code_puppy.plugins.dbos_durable_exec.config import is_enabled
    except (ImportError, ModuleNotFoundError):
        return {"available": False, "enabled": False, "launched": False}
    try:
        return {
            "available": True,
            "enabled": bool(is_enabled()),
            "launched": bool(is_launched()),
        }
    except Exception:
        return {"available": True, "enabled": False, "launched": False}


def can_wrap_predict() -> bool:
    """DBOS may observe only when its existing lifecycle is actually live."""

    return bool(status().get("launched"))
