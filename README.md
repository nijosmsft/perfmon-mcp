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

v0.2.0. Adds the `analyze` mega-tool, registry tools
(`load_log`/`load_csv`/`unload_log`/`log_info`/`list_blgs`),
counter-set / NIC / capture-status discovery tools, a NIC throughput
convenience lens, and LabLink-first remote runbooks (with JSON sidecars)
across every emit-style tool. `load_blg` is preserved as a deprecation
alias of `load_log`.

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

The server exposes 24 tools across six areas:

### Discovery

| Tool | Purpose |
|---|---|
| `list_counter_profiles()` | Table of bundled profiles with when-to-use + overhead. |
| `get_counter_profile(scenario)` | Metadata + counter list + analysis-tool affinity for one profile. |
| `discover_counter_sets(target, vendor_filter)` | List installed PDH counter sets via `Get-Counter -ListSet *`, vendor-tagged (Mellanox / Intel / Broadcom / Microsoft / Other). |
| `parse_counter_sets_output(text, vendor_filter)` | Re-render the same table from raw remote stdout. |
| `discover_nics(target)` | Enumerate NICs via `Get-NetAdapter` (Name, IfIndex, Status, LinkSpeed, MAC, Description). |
| `parse_nics_output(text)` | Re-render the table from raw remote stdout. |

### Live snapshot (PowerShell `Get-Counter`)

| Tool | Purpose |
|---|---|
| `snapshot_counters(scenario, target="local")` | Local: runs `Get-Counter`, returns markdown. Remote: returns LabLink-first runbook + JSON sidecar. |
| `parse_counter_output(text, scenario)` | Parses raw `Get-Counter` text (from remote execution) into the same markdown shape. |

### Capture (logman to .blg, then relog to CSV)

| Tool | Purpose |
|---|---|
| `get_capture_commands(scenario, output_path, duration_s)` | Paste-ready 6-step logman commands (create / start / stop / relog / teardown / verify). |
| `get_capture_instructions(scenario, target, output_path)` | Full runbook including remote transfer-back examples (LabLink preferred, PSRemoting / scp fallbacks). |
| `get_capture_status(target)` | Emit-only `logman query` for the managed collector + LabLink-first JSON sidecar. |
| `parse_capture_status_output(text)` | Render the markdown status table from raw `logman query` stdout. |

### Analyze (load a captured .blg or .csv)

| Tool | Purpose |
|---|---|
| `load_log(path)` | Canonical loader. Auto-detects `.blg` (relog -> CSV) vs `.csv`, hits the side-by-side parquet cache when fresh, otherwise builds it. |
| `load_csv(path)` | Skip relog for plain `.csv` inputs. |
| `load_blg(path)` | **Deprecated v0.2 alias** of `load_log` (still works, emits a `DeprecationWarning`). |
| `list_loaded_logs()` | Active log registry. |
| `log_info(log_id)` | One-log metadata summary (hosts / counter count / duration / cache dir). |
| `unload_log(log_id)` | Drop a log from the registry (cache stays on disk). |
| `list_blgs(directory, pattern)` | Enumerate `.blg` files in a directory (size + mtime). |
| `analyze(log_id, sections)` | Mega-tool that composes summary + timeline + per-queue overview into one call. |
| `get_counter_summary(log_id, top_n)` | Per-counter mean/min/max/p95. |
| `get_counter_timeline(log_id, counter, bucket_seconds, max_rows)` | Time-bucketed values for one counter. |
| `get_per_queue_summary(log_id, queue_filter)` | Per-NIC-RSS-queue aggregation, generic by counter-set. |
| `get_counter_throughput(log_id, nic_filter, top_n)` | NIC convenience lens; narrows the summary to Network Adapter throughput rows. |
| `compare_logs(baseline_log_id, test_log_id, top_n, mode)` | A/B delta table sorted by largest swing. `mode='counter'\|'per_queue'\|'per_cpu'`. |

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
`target="remote"` (emits the LabLink-first runbook + a JSON sidecar).
The sidecar contains transport-agnostic primitives only
(`parse_with`, `shell`, `expected_runtime_s`, `timeout_s`), so any MCP
file-transfer / shell-exec tool can dispatch it.

The canonical remote idiom for every `target='remote'` tool is:

1. Call the tool to get the runbook block.
2. Dispatch the powershell fence via your preferred transport
   (LabLink `lablink.execute_command` / `execute_script` is preferred;
   PSRemoting and scp are fallbacks).
3. Feed the raw stdout to the matching `parse_*_output` tool from the
   sidecar's `parse_with` field; it renders the same markdown the local
   path would have produced.

The transfer-back runbook from `get_capture_instructions` lists three
options in the same preference order:

- **LabLink (or equivalent MCP transport, preferred):**
  `pull_file(node="<name>", remote_path="<remote_blg>", local_path="<local_path>")`
- **PowerShell remoting (fallback):**
  `Copy-Item -FromSession (New-PSSession <host>) -Path <remote_blg> -Destination <local_path>`
- **Manual (fallback):** `scp <user>@<host>:<remote_blg> <local_path>` or a
  human copies the file via RDP / share / USB.

The MCP itself imports no orchestration library and reads no orchestration
environment variable — that contract is enforced by
`tests/test_remote_zero_coupling.py`.

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
