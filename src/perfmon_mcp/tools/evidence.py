"""MCP tools for the evidence-store federation hook.

Exposes two tools:

- :func:`get_evidence_status` - whether the optional ``evidence-store``
  library is installed and whether the ``PERFMON_MCP_EVIDENCE_PATH``
  env var is set. Useful for the operator to diagnose why
  :func:`get_entities` returns no rows.
- :func:`get_entities` - list entities registered for a loaded .blg
  log, optionally filtered by entity_type and a substring filter.

Both tools degrade gracefully when the library is missing or the
env var is unset: they return a friendly message rather than raising.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from perfmon_mcp.app import mcp
from perfmon_mcp.evidence_integration import (
    ENV_VAR,
    db_path_for,
    evidence_root,
    is_available,
    is_configured,
    register_entities_from_log,
)
from perfmon_mcp.formatting.markdown import format_table
from perfmon_mcp.log_state import require_log


_VALID_ENTITY_TYPES = ("machine", "counter_capture")


@mcp.tool()
def get_evidence_status() -> str:
    """Show whether the evidence-store federation hook is available.

    Two independent gates must both be on for entities to be written:
    the optional ``evidence-store`` library must be installed AND the
    ``PERFMON_MCP_EVIDENCE_PATH`` environment variable must point at
    a directory.
    """
    lines = ["**Evidence federation status**", ""]
    lines.append(f"- Library installed: **{is_available()}**")
    lines.append(f"- `{ENV_VAR}` set: **{is_configured()}**")
    root = evidence_root()
    if root is not None:
        lines.append(f"- Evidence root: `{root}`")
    if not is_available():
        lines.append("")
        lines.append(
            "Install the evidence-store library to enable entity "
            "registration. The default `uv sync` install does NOT pull it."
        )
    if is_available() and not is_configured():
        lines.append("")
        lines.append(
            f"Set `{ENV_VAR}=<dir>` and reload logs to register entities."
        )
    return "\n".join(lines)


@mcp.tool()
def get_entities(
    log_id: str,
    entity_type: str = "counter_capture",
    filter: str | None = None,
    max_rows: int = 50,
) -> str:
    """List evidence-store entities registered for a loaded .blg log.

    Args:
        log_id: ID returned by ``load_blg``.
        entity_type: ``"machine"`` or ``"counter_capture"``. Default
            ``"counter_capture"``.
        filter: Optional case-insensitive substring filter applied to
            the entity's primary name column (``hostname`` for
            machines, ``log_id`` for counter captures).
        max_rows: Truncate the table to this many rows. Default 50.

    Returns a markdown table. Returns a friendly message when the
    evidence-store library is unavailable or the env var is unset
    (neither condition is an error).
    """
    log = require_log(log_id)

    if not is_available():
        return (
            "Evidence store is not installed. Install the "
            "evidence-store library to enable this tool."
        )
    if not is_configured():
        return (
            f"Evidence store is installed but `{ENV_VAR}` is unset; "
            "no entities have been recorded for this process."
        )

    et = entity_type.lower().strip()
    if et not in _VALID_ENTITY_TYPES:
        valid = ", ".join(_VALID_ENTITY_TYPES)
        return f"Unknown entity_type `{entity_type}`. Valid: {valid}."

    machine_id = register_entities_from_log(log)
    if machine_id is None:
        return (
            "Evidence registration returned no machine_id for this log. "
            "Check that the .blg counter paths contain a `\\\\HOST\\...` "
            "prefix or pass hostname_override at load time."
        )

    path = db_path_for(machine_id)
    if path is None or not path.exists():
        return f"No evidence DB at `{path}` for machine `{machine_id}`."

    from evidence_store import EvidenceStore  # type: ignore[import-not-found]

    store = EvidenceStore.open(path)
    try:
        df = _query_entities(store, et, filter, machine_id, max_rows)
    finally:
        store.close()

    if df.empty:
        return f"*No `{et}` entities for machine `{machine_id}`.*"

    header = (
        f"**{et} entities** for machine `{machine_id}` (db: `{path}`)\n\n"
    )
    return header + format_table(df, max_rows=max_rows)


def _query_entities(
    store: Any,
    entity_type: str,
    filter_substr: str | None,
    machine_id: str,
    max_rows: int,
) -> pd.DataFrame:
    """Run a per-entity-type SELECT and apply optional filter."""
    if entity_type == "machine":
        sql = "SELECT entity_id, hostname, os_build, architecture FROM Machine WHERE entity_id = ?"
        params: list[Any] = [machine_id]
        if filter_substr:
            sql += " AND LOWER(hostname) LIKE ?"
            params.append(f"%{filter_substr.lower()}%")
    else:
        sql = (
            "SELECT timestamp_utc, payload "
            "FROM Observation "
            "WHERE kind = 'CounterCapture' AND ? = ANY(entity_ids)"
        )
        params = [machine_id]
        if filter_substr:
            sql += " AND LOWER(CAST(payload AS VARCHAR)) LIKE ?"
            params.append(f"%{filter_substr.lower()}%")
    sql += f" LIMIT {int(max_rows) + 1}"
    table_arrow = store.query(sql, params)
    return table_arrow.to_pandas()
