"""Feature-gated, redacted diagnostics for native agents."""

from __future__ import annotations

import sqlite3

from code_puppy.i18n import t

from .config import (
    codeact_enabled,
    database_path,
    diagnostics_enabled,
    is_enabled,
    store_retention_days,
)
from .integrations import dbos
from .state_store import StateStore
from .storage import LATEST_SCHEMA_VERSION, schema_version


def handle_native_command(command: str, name: str):
    """Handle only the `/native` namespace; return None for other commands."""

    if name != "native":
        return None
    parts = command.strip().split()
    subcommand = parts[1].lower() if len(parts) > 1 else "status"
    if subcommand == "status":
        if not is_enabled():
            return t("native_agents.command.disabled")
        return _status()
    if subcommand == "diagnostics":
        if not diagnostics_enabled():
            return t("native_agents.command.diagnostics_disabled")
        return _diagnostics()
    if subcommand == "cleanup":
        if not diagnostics_enabled():
            return t("native_agents.command.diagnostics_disabled")
        return _cleanup()
    return t("native_agents.command.usage")


def native_command_help():
    if not is_enabled():
        return []
    return [("native", t("native_agents.command.help"))]


def _status() -> str:
    return t(
        "native_agents.command.status",
        enabled="on" if is_enabled() else "off",
        codeact="on" if codeact_enabled() else "off",
    )


def _cleanup() -> str:
    try:
        removed = StateStore(str(database_path()), initialize=False).purge_expired(
            store_retention_days()
        )
    except Exception:
        removed = 0
    return t("native_agents.command.cleanup", count=removed)


def _diagnostics() -> str:
    path = database_path()
    ready = False
    version = 0
    executions = 0
    try:
        version = schema_version(path)
        with sqlite3.connect(path) as connection:
            executions = int(
                connection.execute("SELECT COUNT(*) FROM native_executions").fetchone()[
                    0
                ]
            )
        ready = version == LATEST_SCHEMA_VERSION
    except (OSError, sqlite3.Error, ValueError):
        pass
    kennel_enabled = False
    try:
        from code_puppy.plugins.puppy_kennel.state import (
            is_enabled as kennel_is_enabled,
        )

        kennel_enabled = bool(kennel_is_enabled())
    except Exception:
        pass
    dbos_status = dbos.status()
    return t(
        "native_agents.command.diagnostics",
        enabled="on" if is_enabled() else "off",
        codeact="on" if codeact_enabled() else "off",
        storage="ready" if ready else "unavailable",
        schema_version=version,
        executions=executions,
        dbos="launched" if dbos_status.get("launched") else "not launched",
        kennel="on" if kennel_enabled else "off",
    )
