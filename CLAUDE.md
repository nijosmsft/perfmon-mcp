# CLAUDE.md

Guidance for Claude Code (and other AI assistants) working on **perfmon-mcp**
source. For end-user docs (install, MCP config, tool list), see `README.md`.

## What this repo is

An MCP server that wraps `Get-Counter` (PowerShell) and `relog.exe` so AI
assistants can capture and analyze Windows performance counter logs
(`.blg`). Python 3.11+, packaged with `uv`, served over stdio via FastMCP.
Windows-only — every live tool runs PowerShell or `relog.exe` as a
subprocess.

## Layout

```
src/perfmon_mcp/
  server.py              -> entry point: imports tools.* modules then mcp.run("stdio")
  app.py                 -> single FastMCP("perfmon-mcp") + server instructions
  log_state.py           -> LogData dataclass + registry (make_log_id, register/get/require_log)
  evidence_integration.py -> optional evidence-store federation, env-var gated
  tools/                 -> one module per tool group; @mcp.tool() at import time
    profiles.py          -> list_counter_profiles, get_counter_profile
    snapshot.py          -> snapshot_counters, parse_counter_output
    capture.py           -> get_capture_commands, get_capture_instructions
    analyze.py           -> load_blg, list_loaded_logs, get_counter_summary,
                            get_counter_timeline, get_per_queue_summary,
                            compare_logs
    evidence.py          -> get_evidence_status, get_entities
  profiles/              -> ProfileMeta dict + bundled .cset XML
  parsing/
    relog.py             -> shells out to relog.exe, returns DataFrame
    getcounter.py        -> parses Get-Counter PowerShell text output
    aggregator.py        -> counter name normalization, time bucketing, percentile
  formatting/
    markdown.py          -> format_table, format_pct
tests/                   -> pytest, synthetic data only; relog.exe shellout mocked
pyproject.toml           -> hatchling build; deps: mcp, pandas, pyarrow; dev: pytest
```

## How tools get registered

Same pattern as etw-mcp. `server.py` imports every `tools.*` submodule,
each submodule attaches functions to the shared `mcp` instance via
`@mcp.tool()`. **If you add a new tool module, you must add an `import`
line to `server.py` or the tool will not be visible.**

Every `.blg` analysis tool's signature starts with `log_id: str` and
calls `require_log(log_id)` immediately. There is no current-log state —
IDs are explicit so multiple logs can be analyzed concurrently.

## Log lifecycle

```
load_blg(path)
  -> compute log_id (sha256 of path|size|mtime_ns, prefix "log_")
  -> export_dir = <path>.parent / ".perfmon-cache-<stem>"
  -> _load_from_cache: if manifest schema+mtime match, rehydrate from parquet
  -> else relog.exe <path> -o counters.csv -f csv -t 1
     parse CSV into DataFrame
     compute per-counter summary DataFrame
     write counters.parquet + summary.parquet + manifest.json
  -> LogData built and registered, returns markdown summary + log_id
```

`log_id` format: `"log_<sha256[:12]>"` of (lowercase path | size | mtime_ns).
Stable per .blg version. `require_log(log_id)` raises ValueError listing
loaded IDs when unknown — propagate that message.

## Conventions

- **Tool docstrings are user-visible.** FastMCP exposes them as the tool
  description in the MCP protocol. Keep them concrete.
- **Every tool returns a markdown string.** Use `format_table(df)` /
  `format_pct(value)`. Don't return DataFrames or raw dicts.
- **DataFrames live in `LogData.dataframes` keyed by short name**
  (`"counters"`, `"summary"`). Reuse helpers rather than indexing
  directly.
- **No emojis, no decorative output.** Markdown tables and plain headers
  only.
- **Every live-execute tool takes `target: str = "local"`.**
  `"remote"` returns the command, never executes.

## Cache contract

Schema v1, manifest includes `producer in {relog, native, csharp-pdh}`.
The `relog` producer is the day-1 implementation; the other two values
are reserved escape hatches so a future native-PDH or C# sidecar replaces
the shellout without breaking cache compatibility.

## Remote-friendly contract (CRITICAL)

1. Every live-execute tool takes `target: str = "local"`. `"local"`
   executes + returns markdown. `"remote"` returns the
   PowerShell/`logman` commands as a fenced ```powershell``` block.
2. Every emit-style tool has a sibling parser (e.g.
   `parse_counter_output`) that converts raw remote stdout back to the
   same markdown shape.
3. Every file path is an explicit argument; never defaults to a
   local-only location.
4. `get_capture_instructions(scenario, target="remote", ...)` runbook
   names three transports: PowerShell remoting, LabLink (one example
   MCP transport), and manual scp.
5. **ZERO imports of any orchestration library** anywhere in `src/`.
   **ZERO env vars** referencing any orchestration product.

The `test_remote_zero_coupling.py` test asserts this with a literal
`rg lablink src/` check.

## Running and testing

```powershell
# Run the server (stdio, exits on EOF, Ctrl+C interactively)
uv run python -m perfmon_mcp.server

# Tests, synthetic data only, no relog or .blg required
uv run --group dev pytest tests/ -v
```

`uv` manages venv and Python install — don't `pip install` directly.

## Commits

- **All commits must be signed off** (`git commit -s` or include
  `Signed-off-by: <name> <email>` manually).
- Small, single-concern commits.
- Subject line: one short imperative sentence. Body explains *why*.
- **Don't commit unless the user asks** (genesis scaffold is an
  exception — initial bootstrap is committed directly).

## Things to know before changing behavior

- **`load_blg` re-relog is the slow path** (multi-GB .blg can take 30+s).
  Cache hits are sub-second. Anything that silently invalidates the cache
  is a footgun — prefer explicit `force=True` or a `schema_version` bump.
- **Tests don't cover the real `relog.exe` path.** They parse fixture
  CSVs via `parsing/relog._parse_relog_csv` and mock `subprocess.run`.
  End-to-end with a real .blg is manual.
- **Tool count is part of the contract.** Renaming or removing an
  `@mcp.tool()` is a breaking change — bump version in `pyproject.toml`
  and note it.
- **FastMCP's `instructions` string** (in `app.py`) is what clients see
  as server-level guidance. Keep it in sync with the tool set.
