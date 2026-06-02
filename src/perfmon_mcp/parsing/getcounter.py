"""Parser for PowerShell ``Get-Counter`` text output.

``Get-Counter`` (without ``-Format CSV``) emits a localized text block
of the shape:

    Timestamp                 CounterSamples
    ---------                 --------------
    11/30/2026 12:00:00       \\\\HOST\\Processor(_Total)\\% Processor Time :
                              12.34
                              \\\\HOST\\Memory\\Available MBytes :
                              45678

We parse this into a tidy DataFrame so the rest of the MCP can treat
remote-execution stdout exactly the same as local execution.

Multi-sample output (``-MaxSamples > 1``) is supported by repeating the
Timestamp block.

Locale tolerance: the parser does not depend on the column header
strings. It finds ``\\\\machine\\object[(instance)]\\counter : <value>``
pairs and the preceding ISO-ish-or-localized timestamp.
"""

from __future__ import annotations

import re

import pandas as pd


# Match a sample line like ``\\HOST\Processor(_Total)\% Processor Time : 12.34``.
# Backslash-prefixed paths may span lines with the value on the next line;
# we collapse contiguous whitespace before applying the regex so both
# layouts work.
_SAMPLE_RE = re.compile(
    r"""(?P<path>\\\\[^\s][^\\]*\\[^\\]+(?:\([^)]*\))?\\[^:]+?)\s+:\s+(?P<value>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)""",
    re.VERBOSE,
)

# Coarse timestamp matcher - tolerates US (12/31/2026 15:04:05) and
# ISO-ish (2026-12-31 15:04:05) formats; localized variants with am/pm
# get caught by the trailing optional cluster.
_TIMESTAMP_RE = re.compile(
    r"""(?P<ts>
        \d{1,4}[-/]\d{1,2}[-/]\d{1,4}\s+\d{1,2}:\d{2}:\d{2}(?:\s*[AaPp][Mm])?
    )""",
    re.VERBOSE,
)


def parse_get_counter_text(text: str) -> pd.DataFrame:
    """Parse the raw stdout of a PowerShell Get-Counter invocation.

    Returns a DataFrame with columns:

    - ``Timestamp``: pandas datetime (or NaT if no timestamp line found).
    - ``FullPath``: complete ``\\\\machine\\object(instance)\\counter`` path.
    - ``Hostname``: machine portion (uppercase).
    - ``Object``: PDH object name.
    - ``Instance``: instance string, empty when no parens were present.
    - ``Counter``: counter-name portion.
    - ``Value``: numeric reading.

    An empty input or input containing no parseable sample lines returns
    an empty DataFrame with the same columns. The function never raises
    on malformed input - that decision lets the snapshot tool surface
    the original stdout for the user to inspect when parsing fails.
    """
    columns = [
        "Timestamp",
        "FullPath",
        "Hostname",
        "Object",
        "Instance",
        "Counter",
        "Value",
    ]
    if not text or not text.strip():
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []
    current_ts: pd.Timestamp | None = None

    # Split into per-block sections at each timestamp header so multi-
    # sample output groups its samples under the right timestamp.
    # We do this without re.split because we want to remember the
    # timestamp match itself.
    pos = 0
    while pos < len(text):
        ts_match = _TIMESTAMP_RE.search(text, pos)
        if ts_match is None:
            block = text[pos:]
            block_start = pos
            pos = len(text)
        else:
            block_start = ts_match.start()
            # The sample lines for the *previous* block end where this
            # timestamp begins; the new timestamp's samples begin after
            # its match. We handle the initial-segment case below.
            if block_start > pos:
                _emit_samples(text[pos:block_start], current_ts, rows)
            try:
                current_ts = pd.to_datetime(ts_match.group("ts"))
            except (ValueError, TypeError):
                current_ts = None
            pos = ts_match.end()
            continue
        _emit_samples(block, current_ts, rows)

    if not rows:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(rows, columns=columns)
    return df


def _emit_samples(
    block: str,
    timestamp: pd.Timestamp | None,
    rows: list[dict[str, object]],
) -> None:
    """Apply the sample regex to a block and append parsed rows."""
    if not block.strip():
        return
    # Collapse whitespace so two-line wrappings ("path :\n  value") merge.
    flat = re.sub(r"\s+", " ", block)
    for match in _SAMPLE_RE.finditer(flat):
        full_path = match.group("path").strip()
        value_str = match.group("value").strip()
        try:
            value = float(value_str)
        except ValueError:
            continue
        hostname, obj, instance, counter = _split_counter_path(full_path)
        rows.append(
            {
                "Timestamp": timestamp,
                "FullPath": full_path,
                "Hostname": hostname,
                "Object": obj,
                "Instance": instance,
                "Counter": counter,
                "Value": value,
            }
        )


_PATH_RE = re.compile(
    r"^\\\\(?P<host>[^\\]+)\\(?P<obj>[^\\(]+)(?:\((?P<inst>[^)]*)\))?\\(?P<counter>.+)$"
)


def _split_counter_path(full_path: str) -> tuple[str, str, str, str]:
    """Split ``\\\\host\\object[(instance)]\\counter`` into 4-tuple.

    Returns ``("", "", "", full_path)`` when the path doesn't match the
    expected shape - we'd rather emit the row with empty structural
    fields than silently drop it.
    """
    m = _PATH_RE.match(full_path.strip())
    if not m:
        return ("", "", "", full_path)
    host = m.group("host").upper()
    obj = m.group("obj").strip()
    instance = (m.group("inst") or "").strip()
    counter = m.group("counter").strip()
    return host, obj, instance, counter
