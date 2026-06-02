"""Shared helpers for ``target='remote'`` tool output.

Every emit-style tool in perfmon-mcp uses :func:`format_remote_block`
so the markdown shape is identical: a LabLink-first one-paragraph
header, a fenced ``powershell`` block with the command, and a fenced
``json`` sidecar block the LLM can ``json.loads`` and pass directly to
any orchestration tool that accepts ``{node, command, shell, timeout}``.

The sidecar is the canonical machine-readable form of the same command,
so a caller does not have to scrape markdown to wire the command up to
``lablink.execute_command`` (or any equivalent ``Invoke-Command``,
``ssh``, SCP, etc.). It also names the sibling parser, so the round-trip
``emit -> dispatch -> parse_*_output`` is self-describing.

This module is the ONLY place LabLink is named in tool output strings.
It does NOT import any orchestration library and never will - the
``test_remote_zero_coupling.py`` invariant covers this module like
every other.
"""

from __future__ import annotations

import json

# Recommended dispatch transport for `target='remote'` output. LabLink
# leads the runbook because it is the most ergonomic for the project's
# bare-metal lab, but the sidecar JSON is intentionally
# orchestrator-agnostic: any tool that accepts (command, shell, timeout)
# can consume it. PSRemoting and manual SCP remain documented fallbacks.
_REMOTE_HEADER = (
    "**Recommended transport**: LabLink MCP (`lablink.execute_command`) "
    "if the target node is registered. PSRemoting "
    "(`Invoke-Command -ComputerName ...`) is the standard fallback. "
    "Manual SCP / RDP paste is the universal fallback. The JSON sidecar "
    "below carries `command`, `shell`, `timeout_s`, and `parse_with` "
    "so any of these transports can consume it without parsing markdown."
)


def format_remote_block(
    command: str,
    *,
    parse_with: str,
    expected_runtime_s: int,
    timeout_s: int | None = None,
    shell: str = "powershell",
    intro: str = "",
) -> str:
    """Render the standard ``target='remote'`` markdown payload.

    Args:
        command: PowerShell (or other shell) command to run on the
            remote node. Emitted verbatim inside the fenced block.
        parse_with: Name of the sibling ``parse_<X>_output`` tool that
            converts raw stdout from this command back into the same
            markdown the local path would have produced.
        expected_runtime_s: A rough wall-clock estimate of how long the
            command runs under normal conditions. Used by the LLM to
            pick a sensible MCP-side wait window; not authoritative.
        timeout_s: Optional explicit hard timeout for the dispatcher.
            Defaults to ``max(60, expected_runtime_s * 4)`` to give a
            generous cushion above the expected runtime.
        shell: Shell name for the sidecar. Default ``"powershell"``.
        intro: Optional context paragraph rendered before the standard
            header. Use for tool-specific guidance (e.g. "set
            ``-MaxSamples 1`` for snapshot mode").

    Returns:
        A markdown string with one ``powershell`` fence and one ``json``
        sidecar fence. The JSON is valid (``json.loads``-able) and
        contains only primitive values - no Python imports, no
        orchestrator-specific keys.
    """
    if timeout_s is None:
        timeout_s = max(60, int(expected_runtime_s) * 4)

    sidecar = {
        "command": command,
        "shell": shell,
        "timeout_s": int(timeout_s),
        "expected_runtime_s": int(expected_runtime_s),
        "parse_with": parse_with,
    }

    parts: list[str] = []
    if intro:
        parts.append(intro.rstrip())
        parts.append("")
    parts.append(_REMOTE_HEADER)
    parts.append("")
    parts.append(f"```{shell}")
    parts.append(command.rstrip())
    parts.append("```")
    parts.append("")
    parts.append("```json")
    parts.append(json.dumps(sidecar, indent=2))
    parts.append("```")
    return "\n".join(parts) + "\n"


__all__ = ["format_remote_block"]
