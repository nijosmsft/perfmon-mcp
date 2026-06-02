# perfmon-mcp

An MCP server for Windows performance counter (PDH) capture and analysis. It
discovers vendored counter-set profiles, emits paste-ready `logman` /
`Get-Counter` commands for any transport (local, PowerShell remoting,
LabLink, manual scp), and parses captured `.blg` files into pandas for
per-counter / per-queue / A/B analysis.

The server is transport-agnostic. It is not coupled to any orchestration MCP.
Every live-execute tool has a `target="local"` mode that runs the command,
and a `target="remote"` mode that emits the command for the LLM to dispatch
through whatever transport is in scope (PowerShell remoting, an SSH session,
a LabLink MCP node, a human paste). For each emit-style tool there is a
sibling `parse_<X>_output` that takes raw stdout from remote execution and
returns the same markdown shape as the local path.

## Status

v0.1.0. Initial scaffold.

## Setup

Requires Windows (PDH and `relog.exe` are Windows-only) and Python 3.11+.

### Quick install via uv

```powershell
uv run --no-project --with perfmon-mcp perfmon-mcp
```

Or, from a clone:

```powershell
cd C:\git\perfmon-mcp
uv sync
uv run perfmon-mcp
```

### MCP client config

Add the server to your MCP client config (Claude Desktop, Copilot CLI, etc):

```json
{
  "mcpServers": {
    "perfmon-mcp": {
      "command": "uv",
      "args": ["run", "--no-project", "--with", "perfmon-mcp", "perfmon-mcp"]
    }
  }
}
```

## Tool catalog

The server exposes 14 tools across four areas:

### Discovery

| Tool | Purpose |
|---|---|
| `list_counter_profiles()` | Table of bundled profiles with when-to-use + overhead. |
| `get_counter_profile(scenario)` | Metadata + counter list + analysis-tool affinity for one profile. |

### Live snapshot (PowerShell `Get-Counter`)

| Tool | Purpose |
|---|---|
| `snapshot_counters(scenario, target="local")` | Local: runs `Get-Counter`, returns markdown. Remote: returns the command for the LLM to dispatch. |
| `parse_counter_output(text, scenario)` | Parses raw `Get-Counter` text (from remote execution) into the same markdown shape. |

### Capture (logman to .blg, then relog to CSV)

| Tool | Purpose |
|---|---|
| `get_capture_commands(scenario, output_path, duration_s)` | Paste-ready 3-step logman commands (create/start/stop + relog). |
| `get_capture_instructions(scenario, target, output_path)` | Full runbook including remote transfer-back examples. |

### Analyze (load a captured .blg)

| Tool | Purpose |
|---|---|
| `load_blg(path)` | Convert .blg -> CSV via `relog.exe`, register a `log_id`. |
| `list_loaded_logs()` | Active log registry. |
| `get_counter_summary(log_id, top_n)` | Per-counter mean/min/max/p95. |
| `get_counter_timeline(log_id, counter, bucket_seconds, max_rows)` | Time-bucketed values for one counter. |
| `get_per_queue_summary(log_id, queue_filter)` | Per-NIC-RSS-queue aggregation, generic by counter-set. |
| `compare_logs(baseline_log_id, test_log_id, top_n)` | A/B delta table sorted by largest swing. |

### Evidence federation (optional)

| Tool | Purpose |
|---|---|
| `get_evidence_status()` | Whether the `evidence-store` library is installed and `PERFMON_MCP_EVIDENCE_PATH` is set. |
| `get_entities(log_id, entity_type, filter, max_rows)` | List registered entities; no-op when federation is off. |

## Bundled counter profiles

| Scenario | Use for | Overhead notes |
|---|---|---|
| `system-overview` | Lowest-overhead "what is happening" snapshot. | Negligible. |
| `cpu-detailed` | Per-CPU breakdown for hot-CPU diagnosis. | Low. |
| `mellanox-rss` | Per-RSS-queue receive monitoring on Mellanox ConnectX-6 Dx. | Low when total-only; see `mellanox-percpu` for the heavy variant. |
| `mellanox-percpu` | Per-CPU hash-type diagnostics on Mellanox. | **~28pp delivery cost at 400K offered load when actively collected.** Use only for diagnostic distribution analysis, never for perf baselines. |

The Mellanox profiles absorb the standalone `mellanox-rss-metrics` PowerShell
skill into a single discoverable counter-set surface.

## Remote workflows

Any tool that touches the system takes `target="local"` (executes) or
`target="remote"` (emits commands). The remote runbook from
`get_capture_instructions` always documents three transfer-back options so
this MCP is not tied to any one orchestration layer:

- PowerShell remoting:
  `Copy-Item -FromSession (New-PSSession <host>) -Path <remote_blg> -Destination <local_path>`
- LabLink (one example MCP transport — any MCP file-transfer tool works the
  same way): `pull_file(node="<name>", remote_path="<remote_blg>", local_path="<local_path>")`
- Manual: `scp <user>@<host>:<remote_blg> <local_path>` or a human copies
  the file via RDP / share / USB.

The MCP itself imports no orchestration library and reads no orchestration
environment variable.

## Architecture

See `ARCHITECTURE.md` for the lifecycle (live snapshot vs capture vs `.blg`
analysis) and the cache contract.

## Development

```powershell
uv sync
uv run --group dev pytest tests/ -v
```

Tests use synthetic fixtures and never touch real perfmon counters or
`.blg` files. The `relog.exe` shellout is mocked via
`monkeypatch.setattr(subprocess, "run", ...)`.

All commits must be signed off (`git commit -s`).

## License

MIT. See `LICENSE`.
