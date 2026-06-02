"""Evidence-store federation hook for perfmon-mcp.

This module wires loaded .blg perfmon logs into a shared per-machine
``evidence.duckdb`` so the federation MCP (``evidence-query``) can
correlate counter captures with crash dumps / ETW traces / other
evidence for the same Machine.

Gating contract (mirrors etw-mcp G3):

1. ``evidence-store`` is an **optional** dependency. The import is
   wrapped in ``try/except ImportError`` so a default ``uv sync``
   install does NOT pull the library and this module still imports
   cleanly.
2. Registration is **opt-in** via the ``PERFMON_MCP_EVIDENCE_PATH``
   environment variable. When unset,
   :func:`register_entities_from_log` is a no-op. With it set the
   library writes to ``$PERFMON_MCP_EVIDENCE_PATH/<machine_id>/evidence.duckdb``.
3. Any failure inside :func:`register_entities_from_log` is logged and
   swallowed by the call site - load_blg must never break because of
   evidence wiring.

The two gates are independent - the library can be installed but
inactive (no env var), the env var can be set but ineffective (library
missing). Both must hold for entities to be written.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from perfmon_mcp.log_state import LogData

logger = logging.getLogger(__name__)


try:
    from evidence_store import EvidenceStore  # type: ignore[import-not-found]

    _EVIDENCE_AVAILABLE = True
except ImportError:
    EvidenceStore = None  # type: ignore[assignment,misc]
    _EVIDENCE_AVAILABLE = False


ENV_VAR = "PERFMON_MCP_EVIDENCE_PATH"


def is_available() -> bool:
    """Return True when the evidence-store library is importable."""
    return _EVIDENCE_AVAILABLE


def is_configured() -> bool:
    """Return True when the env var is set (regardless of library)."""
    return bool(os.environ.get(ENV_VAR))


def evidence_root() -> Path | None:
    """Return the configured evidence root, or ``None`` if unset."""
    value = os.environ.get(ENV_VAR)
    if not value:
        return None
    return Path(value)


def db_path_for(machine_id: str) -> Path | None:
    """Compute the per-machine DuckDB path for a given machine_id."""
    root = evidence_root()
    if root is None:
        return None
    return root / machine_id / "evidence.duckdb"


# --- Log → identity extraction ---------------------------------------------


def _extract_hostname(log: "LogData") -> str:
    """Pick a single hostname for a log.

    .blg counter paths are ``\\\\HOST\\Object\\Counter``; ``LogData.hostnames``
    is populated by ``_materialize_log_data`` with the distinct host set.
    For machine_id derivation we use the first hostname alphabetically so
    re-runs produce the same machine_id even when the order in the .blg
    differs.
    """
    if log.hostnames:
        return sorted(log.hostnames)[0]
    return ""


def register_entities_from_log(
    log: "LogData",
    *,
    hostname_override: str | None = None,
) -> str | None:
    """Register entities for ``log`` in the configured evidence store.

    Returns the registered ``machine_id``, or ``None`` if either gate
    is not satisfied (library missing OR env var unset).

    Writes one Machine entity plus one ``CounterCapture`` observation
    per loaded .blg, with payload (blg_path, blg_size, sample_count,
    counter_count, duration_seconds, producer).

    ``hostname_override`` is for tests that want to pin a known
    hostname without faking the LogData.hostnames list.
    """
    if not _EVIDENCE_AVAILABLE:
        return None
    if not is_configured():
        return None
    assert EvidenceStore is not None

    hostname = hostname_override or _extract_hostname(log)
    if not hostname:
        logger.warning("evidence: could not determine hostname for %s", log.blg_path)
        return None

    from evidence_store import (  # type: ignore[import-not-found]
        EvidenceRef,
        machine_id as derive_machine_id,
    )

    machine_id = derive_machine_id(hostname)
    path = db_path_for(machine_id)
    if path is None:
        return None

    source_path = str(log.blg_path)
    store = EvidenceStore.open(path)
    try:
        try:
            store.register_machine(hostname=hostname, os_build=None, architecture=None)
        except Exception:
            logger.debug("evidence: register_machine failed for %r", hostname, exc_info=True)

        ref = EvidenceRef(kind="blg_file", path=source_path, locator=log.log_id)
        payload: dict[str, Any] = {
            "log_id": log.log_id,
            "blg_path": source_path,
            "producer": log.producer,
            "sample_count": int(log.sample_count or 0),
            "counter_count": int(log.counter_count or 0),
            "duration_seconds": float(log.duration_seconds or 0.0),
        }
        try:
            store.add_observation(
                kind="CounterCapture",
                entity_ids=[machine_id],
                timestamp_utc=int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000_000),
                payload=payload,
                source=ref,
            )
        except Exception:
            logger.debug(
                "evidence: add CounterCapture observation failed for %s",
                log.log_id, exc_info=True,
            )
        return machine_id
    finally:
        store.close()


def safe_register_entities_from_log(log: "LogData") -> str | None:
    """Like :func:`register_entities_from_log` but never raises.

    The call site (``load_blg``) wants a strict no-op on any failure so
    a broken evidence install cannot regress log loading.
    """
    try:
        return register_entities_from_log(log)
    except Exception:
        logger.warning("evidence: registration failed", exc_info=True)
        return None
